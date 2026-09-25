"""附件相关只读工具

获取试卷图片、学生答题图片等附件信息。
这些工具主要服务于视觉分析能力（如试卷录入）。
通过 ToolContext 获取数据库会话。
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry.tools import ToolDefinition, ToolRegistry
from .tool_context import get_tool_context

logger = logging.getLogger(__name__)


_EXAM_PAPER_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "page": {"type": "integer", "description": "页码（可选，从1开始）"},
    },
    "required": [],
}

_STUDENT_ANSWER_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "question_no": {"type": "integer", "description": "题号（可选）"},
    },
    "required": [],
}

# --- 输出 Schema ---

_EXAM_PAPER_IMAGE_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "total_pages": {"type": "integer"},
                "has_paper_version": {"type": "boolean"},
                "version": {"type": ["integer", "null"]},
                "images": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "integer"},
                            "attachment_id": {"type": "integer"},
                            "storage_name": {"type": ["string", "null"]},
                            "original_name": {"type": ["string", "null"]},
                            "mime_type": {"type": ["string", "null"]},
                            "url": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                },
                "requested_page": {"type": ["integer", "null"]},
            },
            "required": ["exam_id", "total_pages", "has_paper_version",
                         "version", "images", "requested_page"],
        },
        "source": {"type": "string"},
    },
    "required": ["data", "source"],
}

_STUDENT_ANSWER_IMAGE_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "student_id": {"type": "integer"},
                "question_no": {"type": ["integer", "null"]},
                "images": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_no": {"type": ["string", "integer"]},
                            "ocr_status": {"type": "string"},
                            "student_answer_text": {"type": ["string", "null"]},
                            "attachment_id": {"type": "integer"},
                            "storage_name": {"type": ["string", "null"]},
                            "original_name": {"type": ["string", "null"]},
                            "mime_type": {"type": ["string", "null"]},
                            "url": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["exam_id", "student_id", "question_no", "images"],
        },
        "source": {"type": "string"},
    },
    "required": ["data", "source"],
}


def _get_exam_paper_image(
    page: int | None = None, **_: Any
) -> dict[str, Any]:
    """获取试卷图片的访问路径。

    返回：图片 URL 列表、页数信息。
    从 attachments 表查询与考试关联的试卷扫描件。
    """
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    if exam_id is None:
        return {"error": "作用域缺少 exam_id"}

    from sqlalchemy import select
    from ...models.entities import Attachment
    from ...models.agent_entities import ExamPaperVersion

    session = ctx.db_session

    # 从试卷版本中获取附件 ID
    version = session.scalar(
        select(ExamPaperVersion)
        .where(ExamPaperVersion.exam_id == exam_id)
        .order_by(ExamPaperVersion.version.desc())
        .limit(1)
    )

    images: list[dict[str, Any]] = []
    if version and version.source_attachment_ids_json:
        for idx, att_id in enumerate(version.source_attachment_ids_json):
            ctx.check_cancelled()
            att = session.scalar(select(Attachment).where(Attachment.id == att_id))
            if att:
                # 只返回指定页码的图片（如果提供了 page 参数）
                page_no = idx + 1
                if page is not None and page_no != page:
                    continue
                images.append({
                    "page": page_no,
                    "attachment_id": att.id,
                    "storage_name": att.storage_name,
                    "original_name": att.original_name,
                    "mime_type": att.mime_type,
                    "url": f"/api/v1/files/attachments/{att.id}",
                    "status": "processed",
                })

    return {
        "data": {
            "exam_id": exam_id,
            "total_pages": len(images) if version else 0,
            "has_paper_version": version is not None,
            "version": version.version if version else None,
            "images": images,
            "requested_page": page,
        },
        "source": "file_storage:exam_papers",
    }


def _get_student_answer_image(
    question_no: int | None = None,
    **_: Any,
) -> dict[str, Any]:
    """获取学生答题卡图片。

    返回：答题卡图片 URL、标注状态。
    从 attachments 表和 student_item_results 查询。
    """
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "数据库会话未初始化"}
    exam_id = ctx.exam_id
    student_id = ctx.student_id
    if exam_id is None or student_id is None:
        return {"error": "作用域缺少 exam_id 或 student_id"}

    from sqlalchemy import select
    from ...models.entities import Attachment
    from ...models.agent_entities import StudentItemResult, ExamQuestion, ExamPaperVersion

    session = ctx.db_session

    images: list[dict[str, Any]] = []

    # 获取最新试卷版本
    version = session.scalar(
        select(ExamPaperVersion)
        .where(ExamPaperVersion.exam_id == exam_id)
        .order_by(ExamPaperVersion.version.desc())
        .limit(1)
    )

    if version is None:
        return {
            "data": {
                "exam_id": exam_id,
                "student_id": student_id,
                "question_no": question_no,
                "images": [],
                "note": "尚未录入试卷结构",
            },
            "source": "file_storage:answer_sheets",
        }

    # 查询学生小题结果中包含附件的记录
    ctx.check_cancelled()
    query = select(StudentItemResult).where(
        StudentItemResult.exam_id == exam_id,
        StudentItemResult.student_id == student_id,
    )
    if question_no is not None:
        # 根据 question_no 筛选对应的 question_id
        ctx.check_cancelled()
        q = session.scalar(
            select(ExamQuestion).where(
                ExamQuestion.paper_version_id == version.id,
                ExamQuestion.question_no == str(question_no),
            )
        )
        if q:
            query = query.where(StudentItemResult.question_id == q.id)
        else:
            return {
                "data": {
                    "exam_id": exam_id,
                    "student_id": student_id,
                    "question_no": question_no,
                    "images": [],
                    "note": f"题号 {question_no} 不存在",
                },
                "source": "file_storage:answer_sheets",
            }

    ctx.check_cancelled()
    item_results = list(session.scalars(query))

    for ir in item_results:
        # 获取题号信息
        ctx.check_cancelled()
        question = session.scalar(
            select(ExamQuestion).where(ExamQuestion.id == ir.question_id)
        )
        q_no = question.question_no if question else str(ir.question_id)

        img_info: dict[str, Any] = {
            "question_no": q_no,
            "ocr_status": "completed" if ir.student_answer_text else "pending",
            "student_answer_text": ir.student_answer_text,
        }

        # 如果有关联的附件
        if ir.source_attachment_id:
            ctx.check_cancelled()
            att = session.scalar(
                select(Attachment).where(Attachment.id == ir.source_attachment_id)
            )
            if att:
                img_info["attachment_id"] = att.id
                img_info["storage_name"] = att.storage_name
                img_info["original_name"] = att.original_name
                img_info["mime_type"] = att.mime_type
                img_info["url"] = f"/api/v1/files/attachments/{att.id}"

        images.append(img_info)

    return {
        "data": {
            "exam_id": exam_id,
            "student_id": student_id,
            "question_no": question_no,
            "images": images,
        },
        "source": "file_storage:answer_sheets",
    }


def register_attachment_tools(registry: ToolRegistry) -> None:
    """将附件相关工具注册到工具注册表。"""
    registry.register(ToolDefinition(
        name="get_exam_paper_image",
        description="获取试卷图片的访问路径和页数信息",
        parameters_schema=_EXAM_PAPER_IMAGE_SCHEMA,
        output_schema=_EXAM_PAPER_IMAGE_OUTPUT,
        handler=_get_exam_paper_image,
        category="attachment",
        requires_scope=["exam_id"],
        cost_hint=0.02,
    ))
    registry.register(ToolDefinition(
        name="get_student_answer_image",
        description="获取学生答题卡图片",
        parameters_schema=_STUDENT_ANSWER_IMAGE_SCHEMA,
        output_schema=_STUDENT_ANSWER_IMAGE_OUTPUT,
        handler=_get_student_answer_image,
        category="attachment",
        requires_scope=["exam_id", "student_id"],
        cost_hint=0.02,
    ))
