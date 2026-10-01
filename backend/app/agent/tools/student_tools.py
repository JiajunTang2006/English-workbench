"""学生相关只读工具

提供学生个体和班级层面的数据查询能力。
通过 ToolContext 获取数据库会话。
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry.tools import ToolDefinition, ToolRegistry
from .tool_context import get_tool_context

logger = logging.getLogger(__name__)

_STUDENT_LIST_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}
_STUDENT_SCORES_SCHEMA = {
    "type": "object",
    "properties": {"student_ref": {"type": "string", "maxLength": 60},
                   "question_no": {"type": "string", "maxLength": 20}},
    "required": [],
}
_CLASS_RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "top_n": {"type": "integer", "description": "返回前N名，默认50", "default": 50},
    },
    "required": [],
}

# --- 输出 Schema ---

_STUDENT_LIST_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "class_id": {"type": ["integer", "null"]},
                "students": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "student_id": {"type": "integer"},
                            "score": {"type": ["number", "null"]},
                            "name": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "required": ["exam_id", "class_id", "students"],
        },
    },
    "required": ["data"],
}

_STUDENT_SCORES_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "student_id": {"type": "integer"},
                "student_name": {"type": ["string", "null"]},
                "exam_name": {"type": ["string", "null"]},
                "total_score": {"type": ["number", "null"]},
                "class_rank": {"type": ["integer", "null"]},
                "attendance": {"type": ["string", "null"]},
                "item_scores": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_no": {"type": ["string", "integer"]},
                            "score": {"type": ["number", "null"]},
                            "max_score": {"type": "number"},
                        },
                    },
                },
            },
            "required": ["exam_id", "student_id", "student_name", "exam_name",
                         "total_score", "class_rank", "attendance", "item_scores"],
        },
    },
    "required": ["data"],
}

_CLASS_RANKING_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "class_id": {"type": "integer"},
                "ranking": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rank": {"type": "integer"},
                            "student_id": {"type": "integer"},
                            "score": {"type": ["number", "null"]},
                            "name": {"type": ["string", "null"]},
                        },
                        "required": ["rank", "student_id", "score", "name"],
                    },
                },
            },
            "required": ["exam_id", "class_id", "ranking"],
        },
    },
    "required": ["data"],
}


def _get_student_list(**_: Any) -> dict[str, Any]:
    """获取参加考试的学生列表。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}
    class_id = ctx.class_id
    from sqlalchemy import select, distinct
    from ...models.entities import ExamScore, Student
    session = ctx.db_session
    query = select(ExamScore.student_id, ExamScore.total_score, Student.name).join(
        Student, ExamScore.student_id == Student.id, isouter=True
    ).where(ExamScore.exam_id == exam_id)
    if class_id is not None:
        query = query.where(ExamScore.class_id_at_exam == class_id)
    rows = session.execute(query).all()
    return {"data": {"exam_id": exam_id, "class_id": class_id,
        "students": [{"student_id": r[0], "score": r[1], "name": r[2]} for r in rows]}}


def _get_student_scores(student_ref=None, question_no=None, **_: Any) -> dict[str, Any]:
    """获取指定学生在某场考试中的详细成绩。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    student_id = ctx.student_id
    if student_ref:
        from ...services.agent_analysis.conversation import mapper_for_run, roster
        mapper = mapper_for_run(ctx.db_session, ctx.scope)
        student_id = mapper.to_real(student_ref)
        if student_id not in {r["id"] for r in roster(ctx.db_session, ctx.scope)}:
            return {"error": "学生引用不存在或不属于当前范围"}
    if exam_id is None or student_id is None:
        return {"error": "作用域缺少 exam_id 或 student_id"}
    from ...services.student_score_details import get_student_score_details
    ctx.check_cancelled()
    try:
        details = get_student_score_details(
            ctx.db_session, exam_id=exam_id, student_id=student_id,
            class_id=ctx.class_id,
        )
        if question_no is not None:
            details["item_scores"] = [r for r in details["item_scores"] if str(r["question_no"]) == str(question_no)]
        return {"data": details}
    except LookupError as exc:
        return {"error": str(exc)}


def _get_class_ranking(top_n: int = 50, **_: Any) -> dict[str, Any]:
    """获取班级成绩排名。"""
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    class_id = ctx.class_id
    if exam_id is None or class_id is None:
        return {"error": "作用域缺少 exam_id 或 class_id"}
    from sqlalchemy import select
    from ...models.entities import ExamScore, Student
    session = ctx.db_session
    rows = session.execute(
        select(ExamScore.student_id, ExamScore.total_score, Student.name)
        .join(Student, ExamScore.student_id == Student.id, isouter=True)
        .where(ExamScore.exam_id == exam_id, ExamScore.class_id_at_exam == class_id,
               ExamScore.total_score.is_not(None))
        .order_by(ExamScore.total_score.desc())
        .limit(top_n)
    ).all()
    return {"data": {"exam_id": exam_id, "class_id": class_id,
        "ranking": [{"rank": i + 1, "student_id": r[0], "score": r[1], "name": r[2]}
                     for i, r in enumerate(rows)]}}


def register_student_tools(registry: ToolRegistry) -> None:
    """将学生相关工具注册到工具注册表。"""
    registry.register(ToolDefinition(
        name="get_student_list",
        description="获取参加指定考试的学生列表",
        parameters_schema=_STUDENT_LIST_SCHEMA,
        output_schema=_STUDENT_LIST_OUTPUT,
        handler=_get_student_list, category="student", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_student_scores",
        description="获取指定学生的考试详细成绩（各题得分、排名、知识点得分率）",
        parameters_schema=_STUDENT_SCORES_SCHEMA,
        output_schema=_STUDENT_SCORES_OUTPUT,
        handler=_get_student_scores, category="student", requires_scope=["exam_id"]))
    registry.register(ToolDefinition(
        name="get_class_ranking",
        description="获取班级成绩排名",
        parameters_schema=_CLASS_RANKING_SCHEMA,
        output_schema=_CLASS_RANKING_OUTPUT,
        handler=_get_class_ranking, category="student", requires_scope=["exam_id", "class_id"]))
