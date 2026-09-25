"""考试相关只读工具

提供考试统计数据查询，通过 ToolContext 获取数据库会话。
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

from ..registry.tools import ToolDefinition, ToolRegistry
from .tool_context import get_tool_context

logger = logging.getLogger(__name__)

_EXAM_STATS_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}
_QUESTION_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "question_type": {"type": "string", "description": "题型筛选（可选）"},
    },
    "required": [],
}
_SCORE_DIST_SCHEMA = {
    "type": "object",
    "properties": {
        "bin_size": {"type": "integer", "description": "分数段间隔，默认10", "default": 10},
    },
    "required": [],
}
_KNOWLEDGE_COVERAGE_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}
_QUESTION_DIFFICULTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}

# --- 输出 Schema ---

_EXAM_STATS_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "class_id": {"type": ["integer", "null"]},
                "exam_name": {"type": ["string", "null"]},
                "full_score": {"type": "number"},
                "participant_count": {"type": "integer"},
                "average_score": {"type": "number"},
                "max_score": {"type": "number"},
                "min_score": {"type": "number"},
                "std_dev": {"type": "number"},
                "pass_rate": {"type": "number"},
                "excellent_rate": {"type": "number"},
            },
            "required": ["exam_id", "exam_name", "full_score", "participant_count",
                         "average_score", "max_score", "min_score", "std_dev",
                         "pass_rate", "excellent_rate"],
        },
    },
    "required": ["data"],
}

_QUESTION_LIST_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "version": {"type": ["integer", "null"]},
                "paper_status": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_no": {"type": ["string", "integer"]},
                            "question_type": {"type": ["string", "null"]},
                            "section_name": {"type": ["string", "null"]},
                            "max_score": {"type": "number"},
                            "content_text": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "required": ["exam_id", "questions"],
        },
    },
    "required": ["data"],
}

_SCORE_DIST_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "bin_size": {"type": "integer"},
                "full_score": {"type": "number"},
                "distribution": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "range": {"type": "string"},
                            "count": {"type": "integer"},
                        },
                        "required": ["range", "count"],
                    },
                },
            },
            "required": ["exam_id", "bin_size", "distribution"],
        },
    },
    "required": ["data"],
}

_KNOWLEDGE_COVERAGE_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "class_id": {"type": ["integer", "null"]},
                "version": {"type": ["integer", "null"]},
                "paper_status": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
                "participant_count": {"type": "integer"},
                "questions_tagged": {"type": "integer"},
                "questions_total": {"type": "integer"},
                "knowledge_points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": ["string", "null"]},
                            "code": {"type": ["string", "null"]},
                            "question_count": {"type": "integer"},
                            "question_nos": {"type": "array", "items": {"type": ["string", "integer"]}},
                            "total_score": {"type": "number"},
                            "avg_score_rate": {"type": ["number", "null"]},
                        },
                    },
                },
            },
            "required": ["exam_id", "knowledge_points"],
        },
    },
    "required": ["data"],
}

_QUESTION_DIFFICULTY_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "class_id": {"type": ["integer", "null"]},
                "version": {"type": ["integer", "null"]},
                "paper_status": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
                "participant_count": {"type": "integer"},
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_id": {"type": "integer"},
                            "question_no": {"type": ["string", "integer"]},
                            "question_type": {"type": ["string", "null"]},
                            "section_name": {"type": ["string", "null"]},
                            "max_score": {"type": "number"},
                            "knowledge_nodes": {"type": "array", "items": {"type": "string"}},
                            "difficulty_level": {"type": ["string", "null"]},
                            "participant_count": {"type": "integer"},
                            "scored_count": {"type": "integer"},
                            "missing_count": {"type": "integer"},
                            "average_score": {"type": ["number", "null"]},
                            "score_rate": {"type": ["number", "null"]},
                            "full_mark_count": {"type": "integer"},
                            "full_mark_rate": {"type": ["number", "null"]},
                            "wrong_options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "option": {"type": "string"},
                                        "count": {"type": "integer"},
                                    },
                                    "required": ["option", "count"],
                                },
                            },
                        },
                        "required": ["question_no", "max_score", "scored_count"],
                    },
                },
            },
            "required": ["exam_id", "questions"],
        },
    },
    "required": ["data"],
}


def _select_paper_version(session, exam_id: int):
    """选取分析用试卷版本：confirmed 优先，否则回退最新版本并标注状态。

    当前试卷版本没有人工确认入口（仅 MONI 同步直接写 confirmed），
    因此对手工录入的草稿卷回退使用，但把状态透出给模型，避免把草稿当定稿。
    """
    from sqlalchemy import select
    from ...models.agent_entities import ExamPaperVersion
    version = session.scalar(
        select(ExamPaperVersion).where(ExamPaperVersion.exam_id == exam_id)
        .order_by(ExamPaperVersion.version.desc()).limit(1))
    if version is None:
        return None, None, None
    confirmed = session.scalar(
        select(ExamPaperVersion).where(ExamPaperVersion.exam_id == exam_id,
                                       ExamPaperVersion.status == "confirmed")
        .order_by(ExamPaperVersion.version.desc()).limit(1))
    if confirmed is not None:
        return confirmed, confirmed.status, None
    return version, version.status, f"当前使用的是未确认的试卷草稿（v{version.version}），分析结论以该草稿结构为准"


def _participant_ids(session, exam_id: int, class_id: int | None) -> set[int]:
    """实考且有成绩的学生集合（班级口径与 question_metrics 一致）。"""
    from sqlalchemy import select
    from ...models.entities import ExamScore
    filters = [ExamScore.exam_id == exam_id,
               ExamScore.attendance_status == "present",
               ExamScore.total_score.is_not(None)]
    if class_id is not None:
        filters.append(ExamScore.class_id_at_exam == class_id)
    return set(session.scalars(select(ExamScore.student_id).where(*filters)))


def _item_rate(item, max_score: float) -> float | None:
    """单条逐题记录的得分率：优先 score_rate，缺失时用 score/max_score 推算。"""
    if item is None or (item.score is None and item.score_rate is None):
        return None
    if item.score_rate is not None:
        return item.score_rate
    if max_score:
        return item.score / max_score
    return None


def _get_exam_statistics(**_: Any) -> dict[str, Any]:
    """获取当前作用域内的考试统计数据。

    exam_id 决定考试，class_id 决定是否只统计该考试中的某个班级。
    班级必须使用 ExamScore.class_id_at_exam 过滤，不能使用 Student.class_id，
    因为后者是学生当前班级镜像，不能还原历史考试时的班级。
    """
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    class_id = ctx.class_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    from sqlalchemy import select
    from ...models.entities import Exam, ExamScore
    session = ctx.db_session
    ctx.check_cancelled()
    exam = session.scalar(select(Exam).where(Exam.id == exam_id))
    if exam is None:
        return {"error": f"考试 {exam_id} 不存在"}
    ctx.check_cancelled()
    filters = [ExamScore.exam_id == exam_id]
    if class_id is not None:
        filters.append(ExamScore.class_id_at_exam == class_id)
    rows = session.execute(
        select(ExamScore.total_score, ExamScore.attendance_status)
        .where(*filters)
    ).all()
    scores = [r[0] for r in rows if r[0] is not None]
    full_score = exam.full_score or 100.0
    if not scores:
        return {"data": {"exam_id": exam_id, "class_id": class_id, "exam_name": exam.name,
            "full_score": full_score, "participant_count": 0,
            "average_score": 0.0, "max_score": 0.0, "min_score": 0.0,
            "std_dev": 0.0, "pass_rate": 0.0, "excellent_rate": 0.0}}
    avg = statistics.mean(scores)
    std = statistics.stdev(scores) if len(scores) > 1 else 0.0
    pass_line = full_score * 0.6
    excellent_line = full_score * 0.85
    return {"data": {"exam_id": exam_id, "class_id": class_id, "exam_name": exam.name,
        "full_score": full_score, "participant_count": len(scores),
        "average_score": round(avg, 2), "max_score": max(scores),
        "min_score": min(scores), "std_dev": round(std, 2),
        "pass_rate": round(sum(1 for s in scores if s >= pass_line) / len(scores), 4),
        "excellent_rate": round(sum(1 for s in scores if s >= excellent_line) / len(scores), 4)}}


def _get_question_list(question_type: str | None = None, **_: Any) -> dict[str, Any]:
    """获取考试的题目列表。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    from sqlalchemy import select
    from ...models.agent_entities import ExamPaperVersion, ExamQuestion
    session = ctx.db_session
    version, paper_status, note = _select_paper_version(session, exam_id)
    if version is None:
        return {"data": {"exam_id": exam_id, "questions": [], "note": "尚未录入试卷结构"}}
    ctx.check_cancelled()
    query = select(ExamQuestion).where(ExamQuestion.paper_version_id == version.id)
    if question_type:
        query = query.where(ExamQuestion.question_type == question_type)
    query = query.order_by(ExamQuestion.question_no)
    questions = list(session.scalars(query))
    return {"data": {"exam_id": exam_id, "version": version.version,
        "paper_status": paper_status, "note": note,
        "questions": [{"question_no": q.question_no, "question_type": q.question_type,
            "section_name": q.section_name, "max_score": q.max_score,
            "content_text": q.content_text[:200] if q.content_text else None}
            for q in questions]}}


def _get_score_distribution(bin_size: int = 10, **_: Any) -> dict[str, Any]:
    """获取当前作用域内的考试分数段分布。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    class_id = ctx.class_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    from sqlalchemy import select
    from ...models.entities import Exam, ExamScore
    session = ctx.db_session
    exam = session.scalar(select(Exam).where(Exam.id == exam_id))
    full_score = exam.full_score if exam else 100.0
    ctx.check_cancelled()
    filters = [
        ExamScore.exam_id == exam_id,
        ExamScore.total_score.is_not(None),
    ]
    if class_id is not None:
        filters.append(ExamScore.class_id_at_exam == class_id)
    scores = [r[0] for r in session.execute(
        select(ExamScore.total_score).where(*filters)
    ).all()]
    if not scores:
        return {"data": {"exam_id": exam_id, "class_id": class_id,
                         "bin_size": bin_size, "distribution": []}}
    bins: dict[str, int] = {}
    for s in scores:
        lower = int((s // bin_size) * bin_size)
        upper = lower + bin_size
        key = f"{lower}-{upper}"
        bins[key] = bins.get(key, 0) + 1
    distribution = [{"range": k, "count": v} for k, v in sorted(bins.items())]
    return {"data": {"exam_id": exam_id, "class_id": class_id, "bin_size": bin_size,
        "full_score": full_score, "distribution": distribution}}


def _question_node_names(session, question) -> list[str]:
    """题目的知识点名称：MONI 同步的 knowledge_nodes_json ∪ 结构化知识点关联表。"""
    names: list[str] = []
    for node in question.knowledge_nodes_json or []:
        text = str(node).strip()
        if text:
            names.append(text)
    for link in question.knowledge_point_links or []:
        kp = link.knowledge_point
        if kp is not None and kp.name and kp.name not in names:
            names.append(kp.name)
    return names


def _get_knowledge_coverage(**_: Any) -> dict[str, Any]:
    """获取知识点覆盖情况与得分率。

    数据口径：题目知识点取 ExamQuestion.knowledge_nodes_json（MONI 同步）
    与结构化关联表的并集；得分率由逐题作答（StudentItemResult）按知识点
    聚合，按分值加权。不再依赖历史上无人写入的 KnowledgePoint 词表。
    """
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    class_id = ctx.class_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    from collections import defaultdict
    from sqlalchemy import select
    from ...models.agent_entities import ExamQuestion, StudentItemResult
    session = ctx.db_session
    version, paper_status, note = _select_paper_version(session, exam_id)
    if version is None:
        return {"data": {"exam_id": exam_id, "class_id": class_id,
                         "knowledge_points": [], "note": "尚未录入试卷结构"}}
    ctx.check_cancelled()
    questions = list(session.scalars(
        select(ExamQuestion).where(ExamQuestion.paper_version_id == version.id)
        .order_by(ExamQuestion.question_no)))
    participants = _participant_ids(session, exam_id, class_id)
    q_max = {q.id: q.max_score for q in questions}
    rates_by_question: dict[int, list[float]] = defaultdict(list)
    if questions and participants:
        ctx.check_cancelled()
        rows = session.execute(
            select(StudentItemResult).where(
                StudentItemResult.exam_id == exam_id,
                StudentItemResult.student_id.in_(participants) if participants else False,
                StudentItemResult.question_id.in_(list(q_max)),
            )).scalars()
        for item in rows:
            rate = _item_rate(item, q_max.get(item.question_id, 0.0))
            if rate is not None:
                rates_by_question[item.question_id].append(rate)

    kp_data: dict[str, dict] = {}
    tagged = 0
    for question in questions:
        ctx.check_cancelled()
        names = _question_node_names(session, question)
        if names:
            tagged += 1
        q_rates = rates_by_question.get(question.id, [])
        q_rate = round(sum(q_rates) / len(q_rates), 4) if q_rates else None
        for name in names:
            bucket = kp_data.setdefault(name, {
                "name": name, "code": None, "question_count": 0,
                "question_nos": [], "total_score": 0.0, "_weighted": 0.0, "_weight": 0.0})
            bucket["question_count"] += 1
            bucket["question_nos"].append(question.question_no)
            bucket["total_score"] += question.max_score
            if q_rate is not None:
                bucket["_weighted"] += q_rate * question.max_score
                bucket["_weight"] += question.max_score
    knowledge_points = []
    for bucket in kp_data.values():
        weighted = bucket.pop("_weighted", 0.0)
        weight = bucket.pop("_weight", 0.0)
        avg = weighted / weight if weight else None
        bucket["avg_score_rate"] = round(avg, 4) if avg is not None else None
        knowledge_points.append(bucket)
    knowledge_points.sort(key=lambda k: (k["avg_score_rate"] is None, k["avg_score_rate"] if k["avg_score_rate"] is not None else 0))
    note = note or ("试卷结构尚未标注知识点" if tagged == 0 and questions else None)
    return {"data": {"exam_id": exam_id, "class_id": class_id,
        "version": version.version, "paper_status": paper_status, "note": note,
        "participant_count": len(participants),
        "questions_tagged": tagged, "questions_total": len(questions),
        "knowledge_points": knowledge_points}}


def _get_question_difficulty(**_: Any) -> dict[str, Any]:
    """获取逐题难度与答题情况（确定性聚合，来源 services.exams.question_metrics）。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    class_id = ctx.class_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    from collections import Counter
    from sqlalchemy import select
    from ...models.agent_entities import ExamQuestion, StudentItemResult
    from ...services.exams import question_metrics
    session = ctx.db_session
    version, paper_status, note = _select_paper_version(session, exam_id)
    if version is None:
        return {"data": {"exam_id": exam_id, "class_id": class_id,
                         "questions": [], "note": "尚未录入试卷结构"}}
    ctx.check_cancelled()
    try:
        from fastapi import HTTPException
        metrics = question_metrics(session, exam_id, term_id=ctx.term_id,
                                   class_id=class_id, version=version)
    except HTTPException as exc:
        return {"error": str(exc.detail) if getattr(exc, "detail", None) else str(exc)}
    questions = {q.id: q for q in session.scalars(
        select(ExamQuestion).where(ExamQuestion.paper_version_id == version.id))}
    participants = _participant_ids(session, exam_id, class_id)
    wrong_counter: dict[int, Counter] = {}
    if questions and participants:
        ctx.check_cancelled()
        rows = session.execute(
            select(StudentItemResult).where(
                StudentItemResult.exam_id == exam_id,
                StudentItemResult.student_id.in_(participants) if participants else False,
                StudentItemResult.question_id.in_(list(questions)),
            )).scalars()
        for item in rows:
            question = questions.get(item.question_id)
            if question is None:
                continue
            is_wrong = (item.correct is False) or (
                item.correct is None and item.score is not None
                and question.max_score and item.score < question.max_score)
            if is_wrong and item.selected_option:
                wrong_counter.setdefault(item.question_id, Counter())[item.selected_option] += 1

    enriched = []
    for row in metrics:
        question = questions.get(row.get("question_id"))
        options = wrong_counter.get(row.get("question_id"))
        enriched.append({**row,
            "knowledge_nodes": _question_node_names(session, question) if question else [],
            "difficulty_level": question.difficulty_level if question else None,
            "wrong_options": [{"option": opt, "count": cnt}
                              for opt, cnt in (options.most_common(3) if options else [])]})
    return {"data": {"exam_id": exam_id, "class_id": class_id,
        "version": version.version, "paper_status": paper_status, "note": note,
        "questions": enriched}}


def register_exam_tools(registry: ToolRegistry) -> None:
    """将考试相关工具注册到工具注册表。"""
    registry.register(ToolDefinition(
        name="get_exam_statistics",
        description="获取考试的整体统计数据（参考人数、平均分、及格率等）",
        parameters_schema=_EXAM_STATS_SCHEMA,
        output_schema=_EXAM_STATS_OUTPUT,
        handler=_get_exam_statistics, category="exam", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_question_list",
        description="获取考试的题目列表（题号、题型、分值）",
        parameters_schema=_QUESTION_LIST_SCHEMA,
        output_schema=_QUESTION_LIST_OUTPUT,
        handler=_get_question_list, category="exam", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_score_distribution",
        description="获取考试分数段分布",
        parameters_schema=_SCORE_DIST_SCHEMA,
        output_schema=_SCORE_DIST_OUTPUT,
        handler=_get_score_distribution, category="exam", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_knowledge_coverage",
        description="获取知识点覆盖与得分率（按知识点聚合逐题作答，分值加权，薄弱知识点得分率低的排前）",
        parameters_schema=_KNOWLEDGE_COVERAGE_SCHEMA,
        output_schema=_KNOWLEDGE_COVERAGE_OUTPUT,
        handler=_get_knowledge_coverage, category="exam", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_question_difficulty",
        description="获取逐题难度与答题情况（每题得分率、满分人数、缺失人数、高频错误选项、知识点标签）",
        parameters_schema=_QUESTION_DIFFICULTY_SCHEMA,
        output_schema=_QUESTION_DIFFICULTY_OUTPUT,
        handler=_get_question_difficulty, category="exam", requires_scope=["exam_id"]))
