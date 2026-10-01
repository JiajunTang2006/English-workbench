"""分项学业表现（方案 §4.2）。

对同一能力、同一可比测评组计算 ``x = 有效得分 / 满分``，再用指数平滑
``M_t = 0.3 * x_t + 0.7 * M_{t-1}``。没有新记录就不更新，不把缺失填成 0，
也不因为放假自动衰减能力值。

分支必须同时展示观察次数、时间跨度、最近日期和量表来源。样本不足时明确
显示「样本较少」，不标稳定提升。默写只能支撑词汇/拼写，不能自动支撑阅读、
听力能力。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import math
from typing import Any

from sqlalchemy import select

from ...models import ExamPaperVersion, ExamQuestion, KnowledgePoint, QuestionKnowledgePoint, StudentItemResult
from ...models.entities import Exam
from . import events as growth_events
from . import rules


DIMENSION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "writing": ("写作", "作文", "书面表达", "writing", "composition", "essay"),
    "listening": ("听力", "listening", "audio"),
    "reading": ("阅读", "篇章", "完形", "reading", "cloze", "passage"),
    "vocabulary": ("词汇", "拼写", "单词", "选词", "vocabulary", "spelling"),
    "grammar": ("语法", "时态", "从句", "语态", "grammar", "tense", "clause"),
}

# 趋势展示候选阈值（产品提示规则，不是统计显著性判断）。
TREND_THRESHOLD = 0.05


def classify_dimension(texts: list[str]) -> str | None:
    """把题目标签/题型映射到能力分支；无法确定时返回 ``None``（待记录）。"""
    for key in ("writing", "listening", "reading", "vocabulary", "grammar"):
        keywords = DIMENSION_KEYWORDS[key]
        for text in texts:
            lowered = str(text or "").lower()
            if any(keyword in lowered for keyword in keywords):
                return key
    return None


def _dimension_texts(question: ExamQuestion, knowledge_names: list[str]) -> list[str]:
    texts: list[str] = []
    texts.extend(knowledge_names)
    texts.extend(question.knowledge_nodes_json or [])
    texts.extend(question.ability_nodes_json or [])
    texts.extend(question.pitfall_tags_json or [])
    if question.section_name:
        texts.append(question.section_name)
    if question.question_type:
        texts.append(question.question_type)
    return texts


def classify_subject_dimension(ability_nodes: list[str], specs: list[tuple[str, str, tuple[str, ...] | None]], subject=None) -> str | None:
    """Only an explicit, unambiguous ability tag supports a subject ability score.

    A multiple-choice or fill-in question can assess any competency; its format
    must not silently become a student ability judgement.
    """
    if subject is not None:
        from ..subjects import canonical_ability_label
        ability_nodes = [canonical_ability_label(subject, node) for node in ability_nodes or []]
    matches = {
        key for key, label, _question_types in specs
        if any(label.casefold() == str(node).strip().casefold() for node in (ability_nodes or []))
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _empty_dimension(label: str | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "status": "no_evidence",
        "value": None,
        "latest_rate": None,
        "observations": 0,
        "distinct_dates": 0,
        "span_days": None,
        "latest_date": None,
        "scale_source": None,
        "trend": None,
        "sample_note": "暂无该能力分支的可比测评记录",
    }


def build_dimension_performance(session, *, student_id: int, term_id: int) -> dict[str, Any]:
    """按当前学科题型映射，返回学生本学期各表现维度的近期记录。"""
    rule = growth_events.read_rule_version(session, term_id=term_id)
    from ..subjects import get_selected_subject
    subject = get_selected_subject(session)
    if subject.key == "english":
        specs = [(key, rules.DIMENSION_LABELS[key], None) for key in rules.DIMENSION_KEYS]
    else:
        specs = [
            (f"subject_{index}", label,
             tuple(qtype for qtype, mapped in subject.question_dimension_map.items()
                   if mapped == label))
            for index, label in enumerate(subject.analysis_dimensions)
        ]
    dimension_keys = [key for key, _label, _qtypes in specs]

    selected_versions: dict[int, int] = {}
    for version in session.scalars(
        select(ExamPaperVersion)
        .join(Exam, Exam.id == ExamPaperVersion.exam_id)
        .where(Exam.term_id == term_id, Exam.status == "active",
               ExamPaperVersion.status == "confirmed")
        .order_by(ExamPaperVersion.exam_id, ExamPaperVersion.version.desc())
    ):
        selected_versions.setdefault(version.exam_id, version.id)
    if not selected_versions:
        return {key: _empty_dimension(label) for key, label, _qtypes in specs}

    rows = session.execute(
        select(ExamQuestion, StudentItemResult, Exam)
        .join(StudentItemResult, StudentItemResult.question_id == ExamQuestion.id)
        .join(Exam, Exam.id == StudentItemResult.exam_id)
        .where(
            StudentItemResult.student_id == student_id,
            StudentItemResult.exam_id == Exam.id,
            Exam.term_id == term_id,
            Exam.status == "active",
            ExamQuestion.paper_version_id.in_(selected_versions.values()),
            ExamQuestion.included_in_analysis.is_(True),
            StudentItemResult.score.is_not(None),
            StudentItemResult.attendance_status == "present",
        )
        .order_by(Exam.exam_date, StudentItemResult.id)
    ).all()

    question_ids = {question.id for question, _item, _exam in rows}
    knowledge_by_question: dict[int, list[str]] = defaultdict(list)
    if question_ids:
        links = session.execute(
            select(QuestionKnowledgePoint.question_id, KnowledgePoint.name)
            .join(KnowledgePoint, KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id)
            .where(QuestionKnowledgePoint.question_id.in_(question_ids))
        ).all()
        for question_id, name in links:
            knowledge_by_question[question_id].append(name)

    # (dimension, week_key) -> scored points and available points, per item.
    observations: dict[tuple[str, str], list[tuple[date, float, float]]] = defaultdict(list)
    for question, item, exam in rows:
        if question.paper_version_id != selected_versions.get(exam.id):
            continue
        if not question.max_score or question.max_score <= 0:
            continue
        try:
            score = float(item.score)
            maximum = float(question.max_score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score) or not math.isfinite(maximum):
            continue
        score = max(0.0, min(maximum, score))
        texts = _dimension_texts(question, knowledge_by_question[question.id])
        if subject.key == "english":
            dimension = classify_dimension(texts)
        else:
            dimension = classify_subject_dimension(question.ability_nodes_json or [], specs, subject)
        if dimension is None:
            continue
        day = exam.exam_date or rules.business_date_for(item.created_at, rule.timezone)
        week = rules.week_key_for(day)
        observations[(dimension, week)].append((day, score, maximum))

    result: dict[str, Any] = {key: _empty_dimension(label) for key, label, _qtypes in specs}
    by_dimension: dict[str, list[tuple[str, date, float]]] = defaultdict(list)
    for (dimension, week), items in observations.items():
        if dimension not in dimension_keys:
            continue
        week_rate = sum(score for _day, score, _maximum in items) / sum(
            maximum for _day, _score, maximum in items)
        week_day = max(day for day, _score, _maximum in items)
        by_dimension[dimension].append((week, week_day, week_rate))

    for dimension, series in by_dimension.items():
        series.sort(key=lambda entry: (entry[0], entry[1]))
        smoothed: float | None = None
        for _week, _day, rate in series:
            smoothed = rate if smoothed is None else (
                rules.SMOOTHING_ALPHA * rate + (1 - rules.SMOOTHING_ALPHA) * smoothed
            )
        dates = sorted({day for _week, day, _rate in series})
        span = (dates[-1] - dates[0]).days if len(dates) > 1 else 0
        entry = result[dimension]
        entry.update({
            "status": "ok" if len(dates) >= rules.MIN_COMPARABLE_OBSERVATIONS
            else "insufficient_comparable_history",
            "value": round(smoothed, 4) if smoothed is not None else None,
            "latest_rate": round(series[-1][2], 4),
            "observations": len(series),
            "distinct_dates": len(dates),
            "span_days": span,
            "latest_date": dates[-1].isoformat(),
            "scale_source": "exam_item_results",
        })
        entry["trend"], entry["sample_note"] = _trend(series, dates)

    return result


def _trend(series: list[tuple[str, date, float]], dates: list[date]) -> tuple[str | None, str]:
    minimum = rules.MIN_COMPARABLE_OBSERVATIONS
    if len(series) < minimum * 2 or len(dates) < minimum * 2:
        return None, f"可比测评不足 {minimum} 个不同日期，暂不判断稳定提升"
    midpoint = len(series) // 2
    earlier = sum(rate for _week, _day, rate in series[:midpoint]) / midpoint
    later_series = series[midpoint:]
    later = sum(rate for _week, _day, rate in later_series) / len(later_series)
    delta = later - earlier
    if delta >= TREND_THRESHOLD:
        return "up", f"两个不重叠窗口比较上升约 {round(delta * 100)} 个百分点"
    if delta <= -TREND_THRESHOLD:
        return "down", f"两个不重叠窗口比较下降约 {round(abs(delta) * 100)} 个百分点"
    return "flat", "两个不重叠窗口比较变化不明显"
