"""提示词模板与输出契约一致性防回归测试（P2 真机冒烟修复）

背景：真实 DeepSeek 冒烟发现 prompt 模板要求模型输出 evidence_refs/category 等字段，
而 OutputValidator + schema_contract 要求 title/evidence_ids/supports/answer_type，
导致真实模型输出永远校验失败、每次运行降级为统计摘要。

本测试锁定：
1. 每个 prompt 模板必须与对应 capability 的 output_schema 字段命名完全一致；
2. 模板 JSON 示例必须能通过 jsonschema 校验；
3. 模板不得再出现已被契约废弃的字段名。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.app.agent.registry.capabilities import create_default_capability_registry
from backend.app.agent.schema_contract import (
    EXAM_ANALYSIS_OUTPUT_SCHEMA,
    EXAM_INGESTION_OUTPUT_SCHEMA,
    REVIEW_PLAN_OUTPUT_SCHEMA,
    STUDENT_DIAGNOSIS_OUTPUT_SCHEMA,
)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "app" / "agent" / "prompts"

# capability_name -> (prompt 文件, 官方 output_schema)
EXPECTED: dict[str, tuple[str, dict]] = {
    "exam_analysis": ("exam_report.md", EXAM_ANALYSIS_OUTPUT_SCHEMA),
    "student_diagnosis": ("student_diagnosis.md", STUDENT_DIAGNOSIS_OUTPUT_SCHEMA),
    "review_plan": ("review_plan.md", REVIEW_PLAN_OUTPUT_SCHEMA),
    "exam_ingestion": ("exam_extraction.md", EXAM_INGESTION_OUTPUT_SCHEMA),
}

# 契约字段（黑名单：prompt 中不得再出现这些旧字段名）
FORBIDDEN_FIELDS = [
    "evidence_refs",
    "category",
    "student_ref",
    "overall_assessment",
    "knowledge_point",
    "error_cause",
    "plan_summary",
    "priority_knowledge_points",
    "exercises",
    "schedule",
    "exam_title",
    "ocr_confidence",
    "questions_extracted_only",
]

# 契约必需字段（白名单：prompt 必须显式声明）
REQUIRED_FIELDS = ["answer_type", "evidence_ids", "supports", "findings", "recommendations"]


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    assert path.is_file(), f"prompt 模板不存在: {path}"
    return path.read_text(encoding="utf-8")


def _extract_json_blocks(text: str) -> list[str]:
    """从模板中提取所有 ```json ... ``` 代码块。"""
    return re.findall(r"```json\s*\n(.*?)```", text, re.DOTALL)


def _clean_json_example(raw: str) -> dict:
    """清理示例 JSON：替换枚举占位符 / 注释 / 中文说明，得到可解析 JSON。"""
    lines = []
    for line in raw.splitlines():
        # 替换 "a | b | c" 形式的枚举占位符 → 第一个取值
        line = re.sub(r'"([^"]*?)\s*\|\s*[^"]*"', r'"\1"', line)
        # 去掉行内注释说明（如 "high | medium | low" 已在上一行处理，这里处理中文说明）
        line = re.sub(r'"([^"]*?)\s*（.*?）"', r'"\1"', line)
        lines.append(line)
    cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 再尝试：去掉行尾逗号问题
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return json.loads(cleaned)


@pytest.mark.parametrize("capability_name", list(EXPECTED.keys()))
def test_prompt_matches_schema(capability_name: str):
    """prompt 模板字段与官方 schema 一致：JSON 示例能通过 jsonschema 校验。"""
    import jsonschema

    md_name, schema = EXPECTED[capability_name]
    text = _load_prompt(md_name)

    blocks = _extract_json_blocks(text)
    assert blocks, f"{md_name} 缺少 JSON 输出示例代码块"
    # 取最后一个 JSON 块（输出格式示例）
    sample = _clean_json_example(blocks[-1])

    # answer_type 必须与 capability 名称一致
    assert sample.get("answer_type") == capability_name, (
        f"{md_name} 示例 answer_type={sample.get('answer_type')!r}，"
        f"期望 {capability_name!r}"
    )

    # 必须能通过官方 schema 校验
    jsonschema.validate(instance=sample, schema=schema)


@pytest.mark.parametrize("capability_name", list(EXPECTED.keys()))
def test_prompt_forbids_legacy_field_names(capability_name: str):
    md_name, _ = EXPECTED[capability_name]
    text = _load_prompt(md_name)
    # 只检查 JSON 输出示例块内的字段键（"field": 形式），
    # 约束说明文字如「不得输出 evidence_refs」本身可保留字段名。
    example_blocks = "\n".join(_extract_json_blocks(text))
    sample = "\n".join(_extract_json_blocks(text))
    for field in FORBIDDEN_FIELDS:
        assert f'"{field}"' not in sample, (
            f"{md_name} 的 JSON 示例仍包含已废弃字段 {field!r}"
        )
    assert example_blocks, f"{md_name} 缺少 JSON 示例块"


@pytest.mark.parametrize("capability_name", list(EXPECTED.keys()))
def test_prompt_declares_contract_fields(capability_name: str):
    md_name, schema = EXPECTED[capability_name]
    text = _load_prompt(md_name)
    for field in schema.get("properties", {}):
        assert field in text, f"{md_name} 未声明契约字段 {field!r}（output_schema 存在但 prompt 缺失）"


def test_registry_prompt_templates_resolve():
    """注册表中声明的 prompt_template 必须存在且与契约匹配。"""
    registry = create_default_capability_registry()
    for cap in registry.list_all():
        if not cap.prompt_template:
            continue
        path = PROMPTS_DIR / cap.prompt_template
        assert path.is_file(), f"capability {cap.name} 的 prompt 模板缺失: {cap.prompt_template}"
        assert cap.output_schema, f"capability {cap.name} 缺少 output_schema"
        if cap.name in EXPECTED:
            md_name, _ = EXPECTED[cap.name]
            assert cap.prompt_template == md_name


def test_review_plan_timeline_extra_property_declared():
    """review_plan 契约的额外字段 timeline 必须在模板中声明。"""
    text = _load_prompt("review_plan.md")
    assert '"timeline"' in text


def test_exam_ingestion_extra_properties_declared():
    """exam_ingestion 契约的额外字段必须在模板中声明。"""
    text = _load_prompt("exam_extraction.md")
    for field in ("questions_extracted", "confidence", "issues"):
        assert f'"{field}"' in text, f"exam_extraction.md 未声明 {field!r}"