from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Class, Enrollment, Exam, ExamQuestion, ExamScore, Student, StudentItemResult, ExamPaperVersion
from ..models.agent_entities import AgentMessage, AgentSession, AnalysisRun
from ..schemas.exams import StudentExamPoint, StudentProfileRead
from .exams import score_rows
from .student_profiles import apply_analysis_report_to_profile, get_profile_payload
from .subjects import ENGLISH_PAPER_QUESTION_TYPES


QUESTION_TYPE_CATEGORIES = ENGLISH_PAPER_QUESTION_TYPES


def _question_type_category(
    section_name: str | None,
    question_type: str | None,
    categories: tuple[str, ...] = QUESTION_TYPE_CATEGORIES,
) -> str | None:
    """将 MONI 的卷面字段归并到命题解析中的六类题型。"""
    section = str(section_name or "").strip()
    question = str(question_type or "").strip()
    if not section and not question:
        return None
    if categories != QUESTION_TYPE_CATEGORIES:
        def match(value: str) -> str | None:
            # Printed section numbers are not part of a question type.
            normalized = re.sub(r"^\s*(?:第?[一二三四五六七八九十百\d]+[、.．)）]\s*)+", "", value).casefold().replace(" ", "")
            if not normalized:
                return None
            for category in categories:
                if normalized == category.casefold().replace(" ", ""):
                    return category
            # Longer names take precedence (e.g. 综合应用题 over 应用题).
            for category in sorted(categories, key=len, reverse=True):
                name = category.casefold().replace(" ", "")
                stem = name[:-1] if name.endswith("题") else name
                if (name in normalized or len(stem) >= 2 and stem in normalized) and not (f"非{name}" in normalized or f"非{stem}" in normalized):
                    return category
            return None

        # Explicit question type wins over a broader section heading.
        return match(question) or match(section)
    text = f"{section} {question}".strip()
    if "任务型阅读" in text:
        return "阅读理解"
    if "听力" in text or "人机对话" in text:
        return "听力理解"
    if "完形" in text:
        return "完形填空"
    if "阅读" in text:
        return "阅读理解"
    if any(mark in text for mark in ("语法", "grammar")):
        return "语法填空"
    if any(mark in text for mark in ("词汇", "选词", "单词拼写", "词形", "vocabulary")):
        return "词汇运用"
    if any(mark in text for mark in ("作文", "书面表达", "写作", "writing")):
        return "书面表达"
    return None


def question_type_tracking(session: Session, student_id: int, *, term_id: int,
                           scores: list[ExamScore]) -> dict:
    """Compute the chart from confirmed question structure and actual item scores.

    A partial section contributes no rate/mean. Unknown full marks contribute
    raw per-question means, never a fabricated percentage. Old paper versions
    and drafts cannot contribute to the chart.
    """
    from .subjects import get_selected_subject
    from .paper_versions import select_paper_version
    from .paper_distribution import distribution_types
    subject = get_selected_subject(session)
    present = [s for s in scores if s.attendance_status == "present"]
    versions = {}
    for score in present:
        paper, _ = select_paper_version(session, score.exam_id)
        if paper:
            versions[score.exam_id] = paper
    types = distribution_types(subject)
    categories = list(types)
    rows = session.execute(select(
        Exam.id, Exam.name, Exam.exam_date, ExamQuestion.section_name,
        ExamQuestion.question_type, ExamQuestion.max_score,
        StudentItemResult.score, StudentItemResult.source_sync_run_id,
    ).join(ExamPaperVersion, ExamPaperVersion.exam_id == Exam.id)
      .join(ExamQuestion, ExamQuestion.paper_version_id == ExamPaperVersion.id)
      .outerjoin(StudentItemResult,
          (StudentItemResult.question_id == ExamQuestion.id)
          & (StudentItemResult.exam_id == Exam.id)
          & (StudentItemResult.student_id == student_id)
          & (StudentItemResult.attendance_status == "present"))
      .where(Exam.term_id == term_id, Exam.status == "active",
             ExamPaperVersion.id.in_([p.id for p in versions.values()]),
             ExamQuestion.included_in_analysis.is_(True))
      .order_by(ExamQuestion.id)).all()
    grouped, meta = {}, {}
    has_moni = False
    for exam_id, name, day, section, kind, maximum, value, sync_id in rows:
        if versions[exam_id].extraction_provider == "teacher_distribution" and kind in types:
            category = kind
        else:
            category = _question_type_category(section, kind,
                QUESTION_TYPE_CATEGORIES if subject.key == "english" else tuple(subject.question_types))
        if not category:
            continue
        if category not in categories:
            categories.append(category)
        meta[exam_id] = {"exam_name": name, "exam_date": day}
        bucket = grouped.setdefault(exam_id, {}).setdefault(category, {
            "score": 0.0, "max_score": 0.0, "item_count": 0,
            "expected_items": 0, "known_maximum": True})
        bucket["expected_items"] += 1
        bucket["known_maximum"] = bucket["known_maximum"] and maximum is not None and maximum > 0
        bucket["max_score"] += maximum or 0
        if value is not None:
            bucket["score"] += value
            bucket["item_count"] += 1
            has_moni = has_moni or sync_id is not None
    history = []
    for score in sorted(present, key=lambda s: (str(s.exam.exam_date or ""), s.exam_id)):
        values = []
        for category in categories:
            bucket = grouped.get(score.exam_id, {}).get(category)
            if not bucket:
                continue
            complete = bucket["item_count"] == bucket["expected_items"]
            values.append({
                "type": category, "score": round(bucket["score"], 2) if bucket["item_count"] else None,
                "max_score": round(bucket["max_score"], 2) if bucket["known_maximum"] else None,
                "score_rate": round(bucket["score"] / bucket["max_score"] * 100, 2)
                    if complete and bucket["known_maximum"] else None,
                "mean_score": round(bucket["score"] / bucket["item_count"], 2) if complete else None,
                "item_count": bucket["item_count"], "expected_items": bucket["expected_items"],
                "complete": complete,
            })
        if any(v["item_count"] for v in values):
            history.append({"exam_id": score.exam_id, **meta[score.exam_id], "values": values})
    metric = "mean_score" if any(v["max_score"] is None and v["item_count"]
                                 for h in history for v in h["values"]) else "score_rate"
    averages = []
    for name in categories:
        values = [v for h in history for v in h["values"] if v["type"] == name]
        average = {"type": name, "item_count": sum(v["item_count"] for v in values),
                   "exam_count": sum(v[metric] is not None for v in values)}
        for key in ("score_rate", "mean_score"):
            numbers = [v[key] for v in values if v[key] is not None]
            average[key] = round(sum(numbers) / len(numbers), 2) if numbers else None
        averages.append(average)
    return {"categories": categories, "averages": averages, "history": history,
            "exam_count": len(history), "metric_key": metric,
            "metric_label": "各场考试每题平均得分的平均值" if metric == "mean_score" else "各场考试题型得分率的平均值",
            "unit": "分/题" if metric == "mean_score" else "%",
            "incomplete_section_count": sum(not v["complete"] for h in history for v in h["values"]),
            "data_source": ("MONI 小题成绩" if has_moni else "已录入的小题成绩") if history else "暂无小题成绩"}


def student_profile(session: Session, student_id: int, *, term_id: int) -> StudentProfileRead:
    student = session.scalar(
        select(Student).options(selectinload(Student.classroom)).where(Student.id == student_id)
    )
    enrollment = session.scalar(select(Enrollment).where(
        Enrollment.term_id == term_id,
        Enrollment.student_id == student_id,
        Enrollment.status == "active",
    ))
    if not student or not enrollment:
        raise HTTPException(404, "学生不存在")
    classroom = session.get(Class, enrollment.class_id)
    scores = list(session.scalars(
        select(ExamScore)
        .options(selectinload(ExamScore.exam))
        .join(Exam)
        .where(ExamScore.student_id == student_id, Exam.term_id == term_id, Exam.status == "active")
        .order_by(Exam.exam_date, Exam.id)
    ))
    rank_cache: dict[int, dict[int, int | None]] = {}
    row_cache: dict[int, dict[int, object]] = {}
    points: list[StudentExamPoint] = []
    for score in scores:
        if score.exam_id not in rank_cache:
            rows = score_rows(session, score.exam_id, term_id=term_id)
            rank_cache[score.exam_id] = {row.student_id: row.class_rank for row in rows}
            row_cache[score.exam_id] = {row.student_id: row for row in rows}
        row = row_cache[score.exam_id].get(student_id)
        points.append(StudentExamPoint(
            exam_id=score.exam_id,
            exam_name=score.exam.name,
            exam_date=score.exam.exam_date,
            full_score=score.exam.full_score,
            total_score=score.total_score,
            score_rate=round(score.total_score / score.exam.full_score * 100, 2) if score.total_score is not None else None,
            attendance_status=score.attendance_status,
            rank=rank_cache[score.exam_id].get(student_id),
            class_rank=rank_cache[score.exam_id].get(student_id),
            grade_rank=score.grade_rank,
            class_name=row.class_name if row else classroom.name,
            tier=("A" if score.total_score is not None and score.exam.tier_a_cutoff is not None and score.total_score >= score.exam.tier_a_cutoff else
                  "B" if score.total_score is not None and score.exam.tier_b_cutoff is not None and score.total_score >= score.exam.tier_b_cutoff else
                  "C" if score.total_score is not None and score.exam.tier_c_cutoff is not None and score.total_score >= score.exam.tier_c_cutoff else
                  "D" if score.total_score is not None and score.exam.tier_c_cutoff is not None else None),
        ))
    student_data = {
        "id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "class_id": enrollment.class_id,
        "class_name": classroom.name,
        "entrance_english": enrollment.entrance_english,
        "target_score": enrollment.target_score,
        "weak_tags": enrollment.weak_tags,
        "seat": enrollment.seat,
    }
    profile_payload = get_profile_payload(session, student_id, term_id)
    # 兼容在“报告已完成、画像同步修复”之前生成的历史报告：首次打开学生
    # 详情时，将该学生本学期最近一次已完成诊断报告补写为正式画像。
    # 有现成画像时不触碰，避免读取接口覆盖教师已经确认的内容。
    if profile_payload["profile_id"] is None:
        legacy_run = session.scalar(
            select(AnalysisRun)
            .outerjoin(AgentSession, AgentSession.id == AnalysisRun.session_id)
            .where(
                ((AnalysisRun.student_id == student_id) |
                 ((AnalysisRun.student_id.is_(None)) & (AgentSession.student_id == student_id))),
                AnalysisRun.term_id == term_id,
                AnalysisRun.capability.in_(("student_diagnosis", "exam_analysis")),
                AnalysisRun.status.in_(("completed", "degraded")),
            )
            .order_by(AnalysisRun.completed_at.desc(), AnalysisRun.id.desc())
        )
        if legacy_run is not None:
            report = (legacy_run.input_summary_json or {}).get("structured_answer")
            if not isinstance(report, dict):
                # 旧运行可能只在 assistant 消息上保存结构化报告，补查该消息。
                report = session.scalar(
                    select(AgentMessage.structured_answer_json)
                    .where(
                        AgentMessage.analysis_run_id == legacy_run.id,
                        AgentMessage.role == "assistant",
                    )
                    .order_by(AgentMessage.id.desc())
                )
            if isinstance(report, dict):
                try:
                    apply_analysis_report_to_profile(
                        session, run=legacy_run, report=report,
                        confirmed_by="ai", target_student_id=student_id,
                    )
                    profile_payload = get_profile_payload(session, student_id, term_id)
                except Exception:
                    # 学生详情不能因历史报告格式异常而不可用；后续新的报告
                    # 仍会走 submit_report 的同步链路。
                    session.rollback()
    return StudentProfileRead(
        student=student_data,
        exams=points,
        latest_score=points[-1] if points else None,
        question_type_tracking=question_type_tracking(session, student_id, term_id=term_id, scores=scores),
        learning_profile=profile_payload["profile"],
        learning_profile_meta={
            "profile_id": profile_payload["profile_id"],
            "version": profile_payload["version"],
            "updated_at": profile_payload["updated_at"],
            "updated_by": profile_payload["updated_by"],
            "longitudinal_version": profile_payload["longitudinal_version"],
            "needs_compression": profile_payload["needs_compression"],
            "inherited": profile_payload["inherited"],
            "inherited_from_term_id": profile_payload["inherited_from_term_id"],
        },
    )
