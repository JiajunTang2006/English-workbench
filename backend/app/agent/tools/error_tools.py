"""错误分析相关只读工具

查询错误原因评估数据、常见错误统计。
错误原因候选按当前学科配置；历史记录中的实际错因仍以数据库为准。
通过 ToolContext 获取数据库会话。
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry.tools import ToolDefinition, ToolRegistry
from .tool_context import get_tool_context

logger = logging.getLogger(__name__)

def _subject_error_causes(session) -> list[str]:
    from ...services.subjects import get_selected_subject
    return list(get_selected_subject(session).error_causes)

_ERROR_CAUSES_SCHEMA = {
    "type": "object",
    "properties": {
        "question_no": {"type": "integer", "description": "题号（可选）"},
    },
    "required": [],
}
_COMMON_MISTAKES_SCHEMA = {
    "type": "object",
    "properties": {
        "cause": {"type": "string", "description": "错误原因筛选；填写已记录的错因名称。"},
    },
    "required": [],
}

# --- 输出 Schema ---

_ERROR_CAUSES_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "question_no": {"type": ["integer", "null"]},
                "cause_distribution": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cause": {"type": ["string", "null"]},
                            "count": {"type": "integer"},
                            "percentage": {"type": "number"},
                        },
                    },
                },
                "possible_causes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["exam_id", "question_no", "cause_distribution", "possible_causes"],
        },
    },
    "required": ["data"],
}

_COMMON_MISTAKES_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "filter_cause": {"type": ["string", "null"]},
                "common_mistakes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_no": {"type": ["string", "integer"]},
                            "error_count": {"type": "integer"},
                            "top_cause": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "required": ["exam_id", "filter_cause", "common_mistakes"],
        },
    },
    "required": ["data"],
}


def _get_error_causes(question_no: int | None = None, **_: Any) -> dict[str, Any]:
    """获取错误原因分布。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    from sqlalchemy import select, func
    from ...models.agent_entities import ErrorCauseAssessment
    session = ctx.db_session
    query = select(ErrorCauseAssessment.cause, func.count().label("cnt")).where(
        ErrorCauseAssessment.exam_id == exam_id)
    if question_no is not None:
        query = query.where(ErrorCauseAssessment.question_id == question_no)
    query = query.group_by(ErrorCauseAssessment.cause).order_by(func.count().desc())
    rows = session.execute(query).all()
    total = sum(r[1] for r in rows) or 1
    return {"data": {"exam_id": exam_id, "question_no": question_no,
        "cause_distribution": [{"cause": r[0], "count": r[1],
                                 "percentage": round(r[1] / total, 4)} for r in rows],
        "possible_causes": _subject_error_causes(session)}}


def _get_common_mistakes(cause: str | None = None, **_: Any) -> dict[str, Any]:
    """获取常见错误统计。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    from sqlalchemy import select, func
    from ...models.agent_entities import ErrorCauseAssessment, ExamQuestion, ExamPaperVersion
    session = ctx.db_session

    # 获取最新试卷版本
    version = session.scalar(
        select(ExamPaperVersion).where(ExamPaperVersion.exam_id == exam_id)
        .order_by(ExamPaperVersion.version.desc()).limit(1))
    if version is None:
        return {"data": {"exam_id": exam_id, "filter_cause": cause, "common_mistakes": []}}

    # 按题目统计错误数
    ctx.check_cancelled()
    query = select(ErrorCauseAssessment.question_id, func.count().label("cnt")).where(
        ErrorCauseAssessment.exam_id == exam_id)
    if cause:
        query = query.where(ErrorCauseAssessment.cause == cause)
    query = query.group_by(ErrorCauseAssessment.question_id).order_by(func.count().desc()).limit(20)
    rows = session.execute(query).all()

    mistakes = []
    for r in rows:
        ctx.check_cancelled()
        q = session.scalar(select(ExamQuestion).where(ExamQuestion.id == r[0]))
        if q:
            # 获取该题目的主要错因
            ctx.check_cancelled()
            top_cause = session.scalar(
                select(ErrorCauseAssessment.cause).where(
                    ErrorCauseAssessment.question_id == r[0])
                .group_by(ErrorCauseAssessment.cause)
                .order_by(func.count().desc()).limit(1))
            mistakes.append({"question_no": q.question_no, "error_count": r[1],
                             "top_cause": top_cause})

    return {"data": {"exam_id": exam_id, "filter_cause": cause,
        "common_mistakes": mistakes}}


def register_error_tools(registry: ToolRegistry) -> None:
    """将错误分析相关工具注册到工具注册表。"""
    registry.register(ToolDefinition(
        name="get_error_causes",
        description="获取当前学科配置下已记录的错误原因分布",
        parameters_schema=_ERROR_CAUSES_SCHEMA,
        output_schema=_ERROR_CAUSES_OUTPUT,
        handler=_get_error_causes, category="error", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_common_mistakes",
        description="获取常见错误统计（高频错题、典型错误答案）",
        parameters_schema=_COMMON_MISTAKES_SCHEMA,
        output_schema=_COMMON_MISTAKES_OUTPUT,
        handler=_get_common_mistakes, category="error", requires_scope=["exam_id"]))
