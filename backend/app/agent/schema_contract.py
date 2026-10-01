"""统一结构化输出契约

P0-3: 消除三套并行且互相矛盾的输出 Schema。
以 models.py 的 Pydantic 模型为单一真相来源，
自动生成 JSON Schema 供 Capability 定义使用。

统一字段命名（全链路一致）：
  Finding:        title, description, evidence_ids, severity
  Recommendation: action, rationale, supports, priority
  StructuredAnswer: answer_type, summary, profile_summary, findings, recommendations, limitations

前端渲染 (teachmate-views.js) 已与此契约对齐。
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from .models import StructuredAnswer, Finding, Recommendation


# --- 从 Pydantic 模型生成 JSON Schema ---

_finding_schema = TypeAdapter(Finding).json_schema()
_recommendation_schema = TypeAdapter(Recommendation).json_schema()
_structured_answer_schema = TypeAdapter(StructuredAnswer).json_schema()


# --- 共享子 Schema（供各 Capability 组合使用）---

FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "发现标题（简短一行）",
            "minLength": 1,
        },
        "description": {
            "type": "string",
            "description": "详细描述",
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "支撑证据 ID 列表（至少 1 个）",
            "minItems": 1,
        },
        "severity": {
            "type": "string",
            "enum": ["info", "warning", "critical"],
            "description": "严重程度",
        },
    },
    "required": ["title", "evidence_ids"],
    "additionalProperties": False,
}

RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "建议行动（简短一行）",
            "minLength": 1,
        },
        "rationale": {
            "type": "string",
            "description": "建议理由",
        },
        "supports": {
            "type": "array",
            "items": {"type": "string"},
            "description": "支撑证据 ID 列表（至少 1 个）",
            "minItems": 1,
        },
        "priority": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "优先级",
        },
    },
    "required": ["action", "supports"],
    "additionalProperties": False,
}


def make_structured_output_schema(
    answer_type: str,
    *,
    require_findings: bool = False,
    require_recommendations: bool = False,
    extra_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成与 Pydantic StructuredAnswer 对齐的 JSON Schema。

    所有 Capability 的 output_schema 必须使用此函数生成，
    确保字段名、类型和约束全链路一致。

    :param answer_type: 能力名称（如 "exam_analysis"）
    :param require_findings: 是否将 findings 列为 required
    :param require_recommendations: 是否将 recommendations 列为 required
    :param extra_properties: 额外属性（如 exam_ingestion 的 questions_extracted）
    :return: JSON Schema dict
    """
    properties: dict[str, Any] = {
        "answer_type": {
            "type": "string",
            "const": answer_type,
            "description": f"答案类型，固定为 {answer_type}",
        },
        "summary": {
            "type": "string",
            "description": "分析摘要（1-2 句话）",
            "minLength": 1,
        },
        "findings": {
            "type": "array",
            "items": FINDING_SCHEMA,
            "description": "分析发现列表，每项必须有 evidence_ids",
        },
        "recommendations": {
            "type": "array",
            "items": RECOMMENDATION_SCHEMA,
            "description": "教学建议列表，每项必须有 supports",
        },
        "limitations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "分析局限性",
        },
        "scope_snapshot": {
            "type": "object",
            "description": "分析范围快照（由服务器注入，模型应原样回传）",
        },
        "schema_version": {
            "type": "string",
            "description": "报告 Schema 版本",
        },
    }

    if extra_properties:
        properties.update(extra_properties)

    required = ["answer_type", "summary", "scope_snapshot", "schema_version"]
    if require_findings:
        required.append("findings")
    if require_recommendations:
        required.append("recommendations")

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# --- 预生成各能力的标准 Schema ---

EXAM_ANALYSIS_OUTPUT_SCHEMA = make_structured_output_schema(
    "exam_analysis",
    require_findings=True,
    require_recommendations=True,
)

STUDENT_DIAGNOSIS_OUTPUT_SCHEMA = make_structured_output_schema(
    "student_diagnosis",
    require_findings=True,
    require_recommendations=True,
    extra_properties={
        "profile_summary": {
            "type": "string",
            "minLength": 1,
            "description": (
                "结合既有学生画像 JSON 与本次诊断证据重写的教师可读自然语言摘要；"
                "应是一段连贯的话，可包含少量分点，但不要输出 JSON、字段名或机械拼接的句子"
            ),
        },
    },
)

REVIEW_PLAN_OUTPUT_SCHEMA = make_structured_output_schema(
    "review_plan",
    require_findings=True,
    require_recommendations=True,
    extra_properties={
        "timeline": {
            "type": "string",
            "description": "复习时间安排",
        },
        "sections": {
            "type": "array",
            "description": "可直接审核和导出的教学材料分节",
            "minItems": 0,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["lesson_flow", "student_handout", "teacher_key", "followup_assessment"],
                    },
                    "title": {"type": "string", "minLength": 1},
                    "body": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "title", "body", "items"],
                "additionalProperties": False,
            },
        },
    },
)
REVIEW_PLAN_OUTPUT_SCHEMA["required"].extend(["timeline", "sections"])

EXAM_INGESTION_OUTPUT_SCHEMA = make_structured_output_schema(
    "exam_ingestion",
    require_findings=False,
    require_recommendations=False,
    extra_properties={
        "questions_extracted": {
            "type": "integer",
            "description": "提取的题目数量",
        },
        "confidence": {
            "type": "number",
            "description": "OCR 整体置信度（0-1）",
        },
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "提取过程中的问题列表",
        },
    },
)
