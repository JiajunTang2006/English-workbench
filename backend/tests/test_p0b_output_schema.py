"""P0-B 回归测试：工具和 Capability 输出 Schema 校验。

验证：
1. 所有 17 个内置工具都有非 None 的 output_schema
2. 所有 4 个 Capability 都有非空 output_schema
3. 非法输出会被 ToolRegistry.execute() 拦截
4. 合法输出通过校验
"""

from __future__ import annotations

import pytest

from backend.app.agent.registry.tools import ToolRegistry, ToolDefinition, ToolError
from backend.app.agent.registry.capabilities import (
    CapabilityRegistry,
    create_default_capability_registry,
)


# ---------------------------------------------------------------------------
# 1. 工具 output_schema 覆盖率
# ---------------------------------------------------------------------------

class TestToolOutputSchemaCoverage:
    """验证所有内置工具都定义了 output_schema。"""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        from backend.app.agent.registry.tools import create_default_registry
        return create_default_registry()

    def test_all_tools_have_output_schema(self, registry: ToolRegistry) -> None:
        """17 个工具全部必须有 output_schema。"""
        tools = registry.list_all()
        assert len(tools) == 17, f"期望 17 个工具，实际 {len(tools)}"
        missing = [t.name for t in tools if t.output_schema is None]
        assert not missing, f"缺少 output_schema 的工具: {missing}"

    @pytest.mark.parametrize("tool_name", [
        "get_exam_statistics",
        "get_question_list",
        "get_score_distribution",
        "get_knowledge_coverage",
        "get_student_list",
        "get_student_scores",
        "get_class_ranking",
        "get_at_risk_students",
        "get_risk_factors",
        "get_error_causes",
        "get_common_mistakes",
        "get_exam_paper_image",
        "get_student_answer_image",
        "submit_teaching_report",
        "get_student_profile",
        "propose_student_profile_update",
    ])
    def test_specific_tool_has_output_schema(
        self, registry: ToolRegistry, tool_name: str
    ) -> None:
        tool = registry.get(tool_name)
        assert tool is not None, f"工具 {tool_name} 未注册"
        assert tool.output_schema is not None, f"工具 {tool_name} 缺少 output_schema"
        assert "type" in tool.output_schema, f"工具 {tool_name} 的 output_schema 无效"

    def test_output_schemas_are_valid_jsonschema(self, registry: ToolRegistry) -> None:
        """每个 output_schema 本身必须是合法的 JSON Schema。"""
        import jsonschema
        for tool in registry.list_all():
            schema = tool.output_schema
            assert schema is not None
            # 校验 Schema 本身是否合法
            jsonschema.Draft7Validator.check_schema(schema)


# ---------------------------------------------------------------------------
# 2. Capability output_schema 覆盖率
# ---------------------------------------------------------------------------

class TestCapabilityOutputSchemaCoverage:
    """验证所有 Capability 都定义了 output_schema。"""

    @pytest.fixture
    def registry(self) -> CapabilityRegistry:
        return create_default_capability_registry()

    def test_all_capabilities_have_output_schema(
        self, registry: CapabilityRegistry
    ) -> None:
        caps = registry.list_all()
        assert len(caps) == 4, f"期望 4 个能力，实际 {len(caps)}"
        for cap in caps:
            assert cap.output_schema, f"能力 {cap.name} 缺少 output_schema"
            assert "type" in cap.output_schema, f"能力 {cap.name} 的 output_schema 无效"

    @pytest.mark.parametrize("cap_name", [
        "exam_analysis",
        "student_diagnosis",
        "review_plan",
        "exam_ingestion",
    ])
    def test_specific_capability_has_output_schema(
        self, registry: CapabilityRegistry, cap_name: str
    ) -> None:
        cap = registry.get(cap_name)
        assert cap is not None, f"能力 {cap_name} 未注册"
        assert cap.output_schema, f"能力 {cap_name} 缺少 output_schema"

    def test_capability_output_schemas_are_valid_jsonschema(
        self, registry: CapabilityRegistry
    ) -> None:
        import jsonschema
        for cap in registry.list_all():
            jsonschema.Draft7Validator.check_schema(cap.output_schema)


# ---------------------------------------------------------------------------
# 3. 非法输出被拦截
# ---------------------------------------------------------------------------

class TestOutputSchemaEnforcement:
    """验证 ToolRegistry.execute() 会用 output_schema 校验工具输出。"""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        # 注册一个返回非法输出的工具
        reg.register(ToolDefinition(
            name="_test_bad_output",
            description="测试用：返回缺少 data 字段的非法输出",
            parameters_schema={"type": "object", "properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "data": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                    },
                },
                "required": ["data"],
            },
            handler=lambda **kw: {"unexpected_field": 123},
            category="test",
        ))
        # 注册一个返回合法输出的工具
        reg.register(ToolDefinition(
            name="_test_good_output",
            description="测试用：返回合法输出",
            parameters_schema={"type": "object", "properties": {}},
            output_schema={
                "type": "object",
                "properties": {
                    "data": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                    },
                },
                "required": ["data"],
            },
            handler=lambda **kw: {"data": {"value": 42}},
            category="test",
        ))
        return reg

    def test_bad_output_raises_tool_error(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolError) as exc_info:
            registry.execute("_test_bad_output")
        assert "输出 Schema 校验失败" in str(exc_info.value)

    def test_good_output_passes(self, registry: ToolRegistry) -> None:
        result = registry.execute("_test_good_output")
        assert result == {"data": {"value": 42}}

    def test_no_output_schema_skips_validation(self) -> None:
        """output_schema=None 时不校验输出。"""
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="_test_no_schema",
            description="测试用：无 output_schema",
            parameters_schema={"type": "object", "properties": {}},
            output_schema=None,
            handler=lambda **kw: {"anything": "goes"},
            category="test",
        ))
        result = reg.execute("_test_no_schema")
        assert result == {"anything": "goes"}
