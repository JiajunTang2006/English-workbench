"""结构化报告提交工具 (U3-01)

提供 `submit_teaching_report` 工具，供模型提交最终结构化教学报告。
该工具是白名单中唯一的"写入"工具（写入报告结果），其余工具均为只读。

报告 Schema 与 schema_contract.py / models.py 中的统一契约对齐：
- Finding:        title, description, evidence_ids, severity
- Recommendation: action, rationale, supports, priority
- StructuredAnswer: answer_type, summary, findings, recommendations, limitations

服务器注入 run_id，模型不可自行提交。
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry.tools import ToolDefinition
from ..schema_contract import FINDING_SCHEMA, RECOMMENDATION_SCHEMA

logger = logging.getLogger(__name__)


# 结构化报告参数 Schema —— 直接复用 schema_contract 中的统一子 Schema
_REPORT_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer_type": {
            "type": "string",
            "description": "报告类型（如 exam_analysis / student_diagnosis / review_plan）",
        },
        "summary": {
            "type": "string",
            "description": "报告摘要（200 字以内）",
        },
        "profile_summary": {
            "type": "string",
            "description": "学生画像摘要（自然语言段落；仅学生诊断使用）",
        },
        "timeline": {
            "type": "string",
            "description": "复习计划的阶段安排（复习计划使用）",
        },
        "sections": {
            "type": "array",
            "description": "可审核和导出的教学材料分节",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["lesson_flow", "student_handout", "teacher_key", "followup_assessment"],
                    },
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "title", "body", "items"],
                "additionalProperties": False,
            },
        },
        "findings": {
            "type": "array",
            "description": "发现列表，每项必须引用至少一个 evidence_id",
            "items": FINDING_SCHEMA,
        },
        "recommendations": {
            "type": "array",
            "description": "建议列表，每项必须引用至少一个 support",
            "items": RECOMMENDATION_SCHEMA,
        },
        "limitations": {
            "type": "array",
            "description": "报告局限性说明",
            "items": {"type": "string"},
        },
        "scope_snapshot": {
            "type": "object",
            "description": "分析范围快照（由服务器注入，模型应原样回传）",
        },
        "schema_version": {
            "type": "string",
            "description": "报告 Schema 版本",
        },
    },
    "required": [
        "answer_type",
        "summary",
        "findings",
        "recommendations",
        "limitations",
        "scope_snapshot",
        "schema_version",
    ],
}


def _submit_teaching_report(**kwargs: Any) -> dict[str, Any]:
    """提交结构化教学报告。

    该处理函数做基本校验并返回确认信息。
    实际的报告持久化由 AgentLoop / RunExecutor 在工具完成后处理。

    校验项（B2-06 统一契约）：
    - run_id 必须存在于作用域
    - findings 的 evidence_ids 与 recommendations 的 supports：
      非空、存在（内存 EvidenceLedger + 当前 run 的持久化证据）、
      属于当前 run_id、类型在允许白名单内；不允许跨运行引用。
    """
    from .tool_context import get_tool_context

    ctx = get_tool_context()
    if ctx is None:
        return {"error": "工具上下文未初始化"}

    run_id = ctx.run_id
    if run_id is None:
        return {"error": "作用域缺少 run_id"}

    report = kwargs
    findings = report.get("findings", [])
    recommendations = report.get("recommendations", [])

    # B2-06: 统一证据归属校验（内存 ledger + 当前 run 持久化证据 + 跨运行拒绝）
    from ..evidence import validate_report_evidence_references

    ref_errors = validate_report_evidence_references(
        findings,
        recommendations,
        ledger=ctx.evidence_ledger,
        db_session=ctx.db_session,
        run_id=run_id,
    )
    if ref_errors:
        return {
            "error": "报告证据引用校验失败：" + "；".join(ref_errors)
            + "。只能引用当前运行内通过工具返回的证据 ID",
        }

    logger.info(
        "submit_teaching_report: run_id=%d, findings=%d, recommendations=%d",
        run_id, len(findings), len(recommendations),
    )

    return {
        "data": {
            "status": "accepted",
            "run_id": run_id,
            "findings_count": len(findings),
            "recommendations_count": len(recommendations),
        },
    }


def register_report_tools(registry) -> None:
    """注册结构化报告提交工具。"""
    registry.register(ToolDefinition(
        name="submit_teaching_report",
        description=(
            "提交最终结构化教学报告。每项 finding 必须引用至少一个 "
            "evidence_id（来自工具返回的匿名事实），每项 recommendation "
            "必须引用至少一个 support 依据。"
        ),
        parameters_schema=_REPORT_PARAMETERS_SCHEMA,
        handler=_submit_teaching_report,
        category="report",
        requires_scope=["run_id"],
        cost_hint=0.0,
        contains_personal_data=False,
        concurrency_safe=False,
        timeout_seconds=10.0,
        output_schema={
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "run_id": {"type": "integer"},
                        "findings_count": {"type": "integer"},
                        "recommendations_count": {"type": "integer"},
                    },
                },
                "error": {"type": "string"},
            },
        },
    ))
    logger.info("已注册报告提交工具: submit_teaching_report")
