"""风险预警相关只读工具

识别学习风险学生，分析风险因素。
通过 ToolContext 获取数据库会话。
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry.tools import ToolDefinition, ToolRegistry
from .tool_context import get_tool_context

logger = logging.getLogger(__name__)

_AT_RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "threshold": {"type": "number", "description": "风险分数线，默认60", "default": 60},
    },
    "required": [],
}
_RISK_FACTORS_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}

# --- 输出 Schema ---

_AT_RISK_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "threshold": {"type": "number"},
                "at_risk_students": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "student_id": {"type": "integer"},
                            "score": {"type": ["number", "null"]},
                            "name": {"type": ["string", "null"]},
                            "gap": {"type": "number"},
                        },
                    },
                },
            },
            "required": ["exam_id", "threshold", "at_risk_students"],
        },
    },
    "required": ["data"],
}

_RISK_FACTORS_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "student_id": {"type": "integer"},
                "score": {"type": ["number", "null"]},
                "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                "frequent_error_causes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cause": {"type": ["string", "null"]},
                            "count": {"type": "integer"},
                        },
                    },
                },
            },
            "required": ["exam_id", "student_id", "score", "risk_level",
                         "frequent_error_causes"],
        },
    },
    "required": ["data"],
}


def _get_at_risk_students(
    threshold: float = 60, **_: Any
) -> dict[str, Any]:
    """获取低于风险线的学生列表。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    class_id = ctx.class_id
    from sqlalchemy import select
    from ...models.entities import ExamScore, Student
    session = ctx.db_session
    query = select(ExamScore.student_id, ExamScore.total_score, Student.name).join(
        Student, ExamScore.student_id == Student.id, isouter=True
    ).where(ExamScore.exam_id == exam_id,
            ExamScore.total_score.is_not(None),
            ExamScore.total_score < threshold)
    if class_id is not None:
        query = query.where(ExamScore.class_id_at_exam == class_id)
    query = query.order_by(ExamScore.total_score.asc())
    rows = session.execute(query).all()
    return {"data": {"exam_id": exam_id, "threshold": threshold,
        "at_risk_students": [{"student_id": r[0], "score": r[1], "name": r[2],
                              "gap": round(threshold - (r[1] or 0), 2)}
                             for r in rows]}}


def _get_risk_factors(**_: Any) -> dict[str, Any]:
    """分析指定学生的风险因素。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    student_id = ctx.student_id
    if exam_id is None or student_id is None:
        return {"error": "作用域缺少 exam_id 或 student_id"}
    from sqlalchemy import select, func
    from ...models.entities import ExamScore
    from ...models.agent_entities import ErrorCauseAssessment
    session = ctx.db_session

    score = session.scalar(
        select(ExamScore.total_score).where(
            ExamScore.exam_id == exam_id, ExamScore.student_id == student_id))

    # 查询错因分布
    ctx.check_cancelled()
    cause_rows = session.execute(
        select(ErrorCauseAssessment.cause, func.count().label("cnt"))
        .where(ErrorCauseAssessment.exam_id == exam_id,
               ErrorCauseAssessment.student_id == student_id)
        .group_by(ErrorCauseAssessment.cause)
        .order_by(func.count().desc())
    ).all()

    frequent_causes = [{"cause": r[0], "count": r[1]} for r in cause_rows]

    risk_level = "low"
    if score is not None:
        if score < 50:
            risk_level = "high"
        elif score < 70:
            risk_level = "medium"

    return {"data": {"exam_id": exam_id, "student_id": student_id,
        "score": score, "risk_level": risk_level,
        "frequent_error_causes": frequent_causes}}


def register_risk_tools(registry: ToolRegistry) -> None:
    """将风险预警相关工具注册到工具注册表。"""
    registry.register(ToolDefinition(
        name="get_at_risk_students",
        description="获取低于风险分数线的学生列表",
        parameters_schema=_AT_RISK_SCHEMA,
        output_schema=_AT_RISK_OUTPUT,
        handler=_get_at_risk_students, category="risk", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_risk_factors",
        description="分析指定学生的学习风险因素（薄弱知识点、错误原因分布）",
        parameters_schema=_RISK_FACTORS_SCHEMA,
        output_schema=_RISK_FACTORS_OUTPUT,
        handler=_get_risk_factors, category="risk", requires_scope=["exam_id"]))
