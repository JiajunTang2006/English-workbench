"""学生画像工具。

学生诊断完成后，画像变更会自动合并到正式画像；教师可在学生管理中
继续编辑。每次自动合并仍保留一条 confirmed revision 供审计和回溯。
"""

from __future__ import annotations

from typing import Any

from ..registry.tools import ToolDefinition
from .tool_context import get_tool_context
from ...services.student_profiles import (
    confirm_revision,
    create_profile_revision,
    get_profile_payload,
    normalize_patch,
)


_PROFILE_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 2000},
    },
    "additionalProperties": True,
}
for _field in ("strengths", "weaknesses", "habits", "interventions", "goals", "watch_items"):
    _PROFILE_PATCH_SCHEMA["properties"][_field] = {"type": "array", "maxItems": 30}
    _PROFILE_PATCH_SCHEMA["properties"][f"{_field}_add"] = {"type": "array", "maxItems": 30}
    _PROFILE_PATCH_SCHEMA["properties"][f"{_field}_remove"] = {"type": "array", "maxItems": 30}

_PARAMETERS_SCHEMA = {
    "type": "object",
    "properties": {
        "patch": {
            **_PROFILE_PATCH_SCHEMA,
            "description": "画像变更。可使用 summary、strengths_add、weaknesses_add、habits_add、interventions_add、goals_add、watch_items_add 等字段；只提交有依据的变化。",
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 50,
            "description": "支撑本次画像变更的证据 ID",
        },
    },
    "required": ["patch", "evidence_ids"],
    "additionalProperties": False,
}


def _get_student_profile(**_: Any) -> dict[str, Any]:
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "工具上下文未初始化"}
    if ctx.student_id is None or ctx.term_id is None:
        return {"error": "作用域缺少 student_id 或 term_id"}
    payload = get_profile_payload(ctx.db_session, ctx.student_id, ctx.term_id)
    return {"data": {
        "student_id": ctx.student_id,
        "term_id": ctx.term_id,
        "profile": payload["profile"],
        "version": payload["version"],
        "updated_at": payload["updated_at"],
        "longitudinal_profile": payload["longitudinal_profile"],
        "longitudinal_version": payload["longitudinal_version"],
        "continuity": {
            "inherited": payload["inherited"],
            "inherited_from_term_id": payload["inherited_from_term_id"],
            "needs_compression": payload["needs_compression"],
        },
    }}


def _propose_student_profile_update(*, patch: dict[str, Any], evidence_ids: list[str] | None = None, **_: Any) -> dict[str, Any]:
    ctx = get_tool_context()
    if ctx is None or ctx.db_session is None:
        return {"error": "工具上下文未初始化"}
    if ctx.student_id is None or ctx.term_id is None:
        return {"error": "作用域缺少 student_id 或 term_id"}
    try:
        evidence_ids = list(dict.fromkeys(
            str(item).strip() for item in (evidence_ids or [])
            if item is not None and str(item).strip()
        ))
        if not evidence_ids:
            return {"error": "画像变更必须引用本次分析的证据 ID"}
        if ctx.evidence_ledger is None or not ctx.evidence_ledger.validate_references(evidence_ids):
            invalid = [item for item in evidence_ids if ctx.evidence_ledger is None or not ctx.evidence_ledger.exists(item)]
            return {"error": "画像变更引用了不存在的证据 ID：" + "、".join(invalid)}
        normalized = normalize_patch(patch)
        if not normalized:
            return {"error": "画像变更不能为空"}
        revision = create_profile_revision(
            ctx.db_session,
            student_id=ctx.student_id,
            term_id=ctx.term_id,
            patch=normalized,
            analysis_run_id=ctx.run_id,
            evidence_ids=evidence_ids,
        )
        # 新流程：分析结果直接进入正式画像；revision 仍保留为 confirmed
        # 审计记录，教师后续可在学生管理中继续修改。
        revision = confirm_revision(ctx.db_session, revision.id, confirmed_by="ai")
        payload = get_profile_payload(ctx.db_session, ctx.student_id, ctx.term_id)
        return {
            "data": {
                "status": "applied",
                "revision_id": revision.id,
                "student_id": ctx.student_id,
                "term_id": ctx.term_id,
                "base_version": revision.base_version,
                "patch": normalized,
                "current_profile": payload["profile"],
                "message": "画像变更已自动写入正式画像，教师可在学生管理中继续修改。",
            }
        }
    except ValueError as exc:
        return {"error": f"画像变更无效：{exc}"}


def register_profile_tools(registry) -> None:
    registry.register(ToolDefinition(
        name="get_student_profile",
        description="读取当前学生当前学期画像和跨学期长期画像；画像不是成绩事实，成绩仍需调用成绩工具获取。",
        parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_get_student_profile,
        category="student_profile",
        requires_scope=["student_id", "term_id"],
        cost_hint=0.0,
        contains_personal_data=True,
        concurrency_safe=True,
        timeout_seconds=10.0,
        output_schema={"type": "object", "properties": {"data": {"type": "object"}, "error": {"type": "string"}}},
    ))
    registry.register(ToolDefinition(
        name="propose_student_profile_update",
        description=(
            "根据当前学生诊断和已获取证据，提出学生画像的结构化变更。"
            "该工具会把有证据支持的变更自动写入正式画像，并保留 confirmed 审计记录；"
            "教师可在学生管理中继续修改。必须先读取学生数据，"
            "并在 evidence_ids 中引用本次分析依据。"
        ),
        parameters_schema=_PARAMETERS_SCHEMA,
        handler=_propose_student_profile_update,
        category="student_profile",
        requires_scope=["student_id", "term_id", "run_id"],
        cost_hint=0.0,
        contains_personal_data=True,
        concurrency_safe=False,
        timeout_seconds=10.0,
        output_schema={
            "type": "object",
            "properties": {"data": {"type": "object"}, "error": {"type": "string"}},
        },
    ))
