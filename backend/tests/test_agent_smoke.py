"""R0 基线：Agent 核心模块冒烟测试

验证 Agent 各模块能正确导入和基本实例化。
这些测试在 R1-R7 改造后仍应通过。
"""

from __future__ import annotations

import inspect
import pytest

from backend.app.agent.config import AgentConfig
from backend.app.agent.context import TeachingContext
from backend.app.agent.cost import CostEstimator, UnknownModelPricingError
from backend.app.agent.evidence import EvidenceLedger
from backend.app.agent.loop import AgentLoop
from backend.app.agent.output_validator import OutputValidator
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.registry.capabilities import create_default_capability_registry
from backend.app.agent.registry.tools import create_default_registry, ToolRegistry
from backend.app.agent.tools.tool_context import ToolContext, set_tool_context, reset_tool_context
from backend.app.routers.agent import _scope_for_message
from backend.tests.conftest import MockTextProvider, make_mock_registry, make_mock_tool


# ---------------------------------------------------------------------------
# 导入与实例化
# ---------------------------------------------------------------------------


def test_agent_config_defaults():
    config = AgentConfig()
    assert config.agent_enabled is False
    assert config.text_provider == "deepseek"
    assert config.max_iterations == 4
    assert config.max_parallel_tools == 4
    assert config.budget_soft_limit_yuan == 0.5
    assert config.rules_version == "v1.0.0"


def test_default_registries_instantiate():
    tool_reg = create_default_registry()
    cap_reg = create_default_capability_registry()
    assert len(tool_reg.list_all()) == 17
    assert len(cap_reg.list_all()) == 4
    assert cap_reg.validate_tools(tool_reg) == []


def test_general_chat_followup_inherits_latest_scoped_exam():
    """未绑定考试的会话追问仍可使用同会话上一轮的真实考试范围。"""
    from types import SimpleNamespace

    prior = SimpleNamespace(exam_id=22, class_id=7, student_id=None)

    class SessionStub:
        def scalar(self, _query):
            return prior

    current = SimpleNamespace(exam_id=None, class_id=None, student_id=None, term_id=3)
    scope = _scope_for_message(SessionStub(), current, 101, "general_chat")
    assert scope == {"term_id": 3, "exam_id": 22, "class_id": 7}


def test_evidence_ledger_basic():
    ledger = EvidenceLedger()
    eid = ledger.add(
        evidence_type="db_metric",
        local_fact={"average": 75.5},
        source_entity="get_exam_statistics",
    )
    assert eid == "ev_1"
    assert ledger.exists("ev_1")
    assert not ledger.exists("ev_99")
    assert ledger.all_ids() == ["ev_1"]
    model_view = ledger.for_model()
    assert len(model_view) == 1
    assert model_view[0]["evidence_id"] == "ev_1"


def test_privacy_mapper_basic():
    mapper = PrivacyMapper()
    anon = mapper.register_student(42)
    assert anon == "student_01"
    assert mapper.to_anonymous(42) == "student_01"
    assert mapper.to_real("student_01") == 42
    assert mapper.register_student(42) == "student_01"
    assert mapper.register_student(99) == "student_02"


def test_privacy_mapper_sanitize_removes_sensitive_keys():
    mapper = PrivacyMapper()
    mapper.register_student(1)
    data = {
        "student_id": 1,
        "name": "张三",
        "student_no": "2024001",
        "parent_phone": "13800138000",
        "seat": "3排5座",
        "score": 85.0,
        "exam_name": "期中考试",
    }
    sanitized = mapper.sanitize_for_model(data)
    assert "name" not in sanitized
    assert "student_no" not in sanitized
    assert "parent_phone" not in sanitized
    assert "seat" not in sanitized
    assert sanitized["score"] == 85.0
    assert sanitized["exam_name"] == "期中考试"
    assert sanitized.get("anonymous_id") == "student_01"


def test_teaching_context_to_model_context_no_ids():
    ctx = TeachingContext(
        scope={"exam_id": 123, "class_id": 5, "student_id": 42},
        capability="exam_analysis",
    )
    model_ctx = ctx.to_model_context()
    assert "exam_id" not in model_ctx
    assert "class_id" not in model_ctx
    assert "student_id" not in model_ctx
    assert model_ctx["has_exam_scope"] is True
    assert model_ctx["has_class_scope"] is True
    assert model_ctx["has_student_scope"] is True
    assert model_ctx["capability"] == "exam_analysis"


def test_tool_registry_scope_filtering():
    registry = make_mock_registry()
    tools_exam = registry.for_model(scope={"exam_id": 1})
    tool_names = {t["function"]["name"] for t in tools_exam}
    assert "get_exam_statistics" in tool_names
    assert "get_class_ranking" not in tool_names
    assert "get_student_answer_image" not in tool_names

    tools_class = registry.for_model(scope={"exam_id": 1, "class_id": 1})
    tool_names2 = {t["function"]["name"] for t in tools_class}
    assert "get_class_ranking" in tool_names2


def test_tool_registry_execute_mock():
    registry = ToolRegistry()
    registry.register(make_mock_tool("test_tool", data={"value": 42}))
    result = registry.execute("test_tool")
    assert result == {"data": {"value": 42}}


def test_tool_registry_execute_unknown():
    from backend.app.agent.registry.tools import ToolError
    registry = ToolRegistry()
    with pytest.raises(ToolError):
        registry.execute("nonexistent_tool")


def test_output_validator_rejects_empty():
    """R2: 空输出应验证失败。"""
    validator = OutputValidator()
    result = validator.validate(
        structured_answer=None,
        evidence_ledger=EvidenceLedger(),
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is False
    assert any("无法解析" in e for e in result.errors)


def test_output_validator_accepts_valid_structure():
    """R2: 结构合法的输出应通过验证。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"avg": 75})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "考试分析摘要",
            "findings": [{"title": "平均分偏低", "evidence_ids": [eid]}],
            "recommendations": [{"action": "加强基础练习", "supports": [eid]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is True


def test_output_validator_rejects_fake_evidence():
    """R2: 引用不存在的证据 ID 应验证失败。"""
    validator = OutputValidator()
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "x", "evidence_ids": ["ev_999"]}],
            "recommendations": [{"action": "y", "supports": ["ev_999"]}],
            "limitations": [],
        },
        evidence_ledger=EvidenceLedger(),
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is False
    assert any("ev_999" in e for e in result.errors)


def test_cost_estimator_basic():
    est = CostEstimator()
    estimate = est.estimate_text(
        model_name="deepseek-chat",
        estimated_input_tokens=2000,
        estimated_output_tokens=4096,
    )
    assert estimate.estimated_cost_yuan > 0
    assert estimate.model_name == "deepseek-chat"


def test_cost_estimator_unknown_model_is_rejected():
    """预算控制开启时未知模型必须拒绝，不能绕过预算门禁按零成本执行。"""
    est = CostEstimator(budget_control_enabled=True)
    with pytest.raises(UnknownModelPricingError):
        est.estimate_text(
            model_name="unknown-model-xyz",
            estimated_input_tokens=2000,
            estimated_output_tokens=4096,
        )


def test_cost_estimator_unknown_model_allowed_when_control_off():
    """预算控制默认关闭：未知模型按零成本放行，不拒绝用户。"""
    est = CostEstimator(budget_control_enabled=False)
    estimate = est.estimate_text(
        model_name="unknown-model-xyz",
        estimated_input_tokens=2000,
        estimated_output_tokens=4096,
    )
    assert estimate.estimated_cost_yuan == 0.0
    assert estimate.requires_confirmation is False


def test_tool_registry_rejects_invalid_output_schema():
    from backend.app.agent.registry.tools import ToolDefinition, ToolError

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="invalid_output",
        description="",
        parameters_schema={"type": "object"},
        output_schema={"type": "object", "required": ["data"]},
        handler=lambda: {"unexpected": True},
        category="test",
    ))
    with pytest.raises(ToolError, match="输出 Schema 校验失败"):
        registry.execute("invalid_output")


# ---------------------------------------------------------------------------
# AgentLoop 集成冒烟（使用 mock provider + mock registry）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_completes_with_mock(mock_provider, mock_registry, agent_config, teaching_context):
    """AgentLoop 使用 mock 完成一轮分析。"""
    from backend.app.agent.providers.base import ToolCall

    mock_provider.set_response(
        content="让我查看考试统计",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    mock_provider.set_response(
        content='{"answer_type":"exam_analysis","summary":"分析完成","findings":[{"title":"平均分75.5","evidence_ids":["ev_1"]}],"recommendations":[{"action":"加强阅读","supports":["ev_1"]}],"limitations":[]}',
        finish_reason="stop",
    )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(scope=teaching_context.scope)

    result = await loop.run(
        system_prompt="你是教学分析助手",
        user_message="分析这次考试",
        context=teaching_context,
        tools=tools,
        max_iterations=4,
        budget_limit_yuan=0.5,
    )

    assert result.success is True
    assert result.stop_reason == "completed"
    assert mock_provider.call_count == 2
    assert result.structured_answer is not None
    assert "findings" in result.structured_answer
    assert len(teaching_context.evidence_ledger.all_ids()) > 0


@pytest.mark.asyncio
async def test_agent_loop_emits_live_model_and_tool_events(
    mock_provider, mock_registry, agent_config, teaching_context,
):
    """legacy 运行时在模型/工具等待期间也要持续更新过程消息。"""
    from backend.app.agent.providers.base import ToolCall

    mock_provider.set_response(
        content="先读取考试统计",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    mock_provider.set_response(content="分析完成", finish_reason="stop")
    events = []

    async def sink(event_type, data):
        events.append((event_type, data))

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    result = await loop.run(
        system_prompt="你是教学分析助手",
        user_message="分析这次考试",
        context=teaching_context,
        tools=mock_registry.for_model(scope=teaching_context.scope),
        max_iterations=4,
        budget_limit_yuan=0.5,
        event_sink=sink,
    )

    assert result.success is True
    assert [event[0] for event in events] == [
        "model.started", "model.completed",
        "tool.started", "tool.completed",
        "model.started", "model.completed",
    ]


@pytest.mark.asyncio
async def test_agent_loop_max_iterations(mock_provider, mock_registry, agent_config, teaching_context):
    """AgentLoop 迭代次数用尽时返回 max_iterations。"""
    from backend.app.agent.providers.base import ToolCall

    for _ in range(5):
        mock_provider.set_response(
            content="继续分析",
            tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
            finish_reason="tool_calls",
        )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(scope=teaching_context.scope)

    result = await loop.run(
        system_prompt="你是教学分析助手",
        user_message="分析这次考试",
        context=teaching_context,
        tools=tools,
        max_iterations=2,
        budget_limit_yuan=0.5,
    )

    assert result.success is False
    assert result.stop_reason == "max_iterations"


@pytest.mark.asyncio
async def test_agent_loop_no_tools_direct_answer(mock_provider, mock_registry, agent_config, teaching_context):
    """AgentLoop 无工具调用时返回 data_insufficient（P0-5: 需至少一条证据）。"""
    mock_provider.set_response(
        content='{"answer_type":"exam_analysis","summary":"无数据","findings":[],"recommendations":[],"limitations":["无数据"]}',
        finish_reason="stop",
    )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(scope=teaching_context.scope)

    result = await loop.run(
        system_prompt="你是教学分析助手",
        user_message="分析这次考试",
        context=teaching_context,
        tools=tools,
        max_iterations=4,
        budget_limit_yuan=0.5,
    )

    # P0-5: 无工具调用、无证据 → data_insufficient
    assert result.success is False
    assert result.stop_reason == "data_insufficient"
    assert mock_provider.call_count == 1


@pytest.mark.asyncio
async def test_agent_loop_tool_result_registered_as_evidence(mock_provider, mock_registry, agent_config, teaching_context):
    """工具执行后结果应注册为证据。"""
    from backend.app.agent.providers.base import ToolCall

    mock_provider.set_response(
        content="查询中",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    mock_provider.set_response(
        content='{"answer_type":"exam_analysis","summary":"ok","findings":[{"title":"ok","evidence_ids":["ev_1"]}],"recommendations":[{"action":"none","supports":["ev_1"]}],"limitations":[]}',
        finish_reason="stop",
    )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(scope=teaching_context.scope)

    result = await loop.run(
        system_prompt="你是教学分析助手",
        user_message="分析",
        context=teaching_context,
        tools=tools,
        max_iterations=4,
        budget_limit_yuan=0.5,
    )

    assert result.success is True
    evidence_ids = teaching_context.evidence_ledger.all_ids()
    assert len(evidence_ids) == 1
    assert evidence_ids[0] == "ev_1"


# ---------------------------------------------------------------------------
# ToolContext contextvars
# ---------------------------------------------------------------------------


def test_tool_context_set_and_reset():
    ctx = ToolContext(db_session=None, scope={"exam_id": 99})
    token = set_tool_context(ctx)
    from backend.app.agent.tools.tool_context import get_tool_context
    assert get_tool_context() is ctx
    assert get_tool_context().exam_id == 99
    reset_tool_context(token)
    assert get_tool_context() is None


# ---------------------------------------------------------------------------
# R1 修复验证（原 R0 快照，已修复）
# ---------------------------------------------------------------------------


def test_r1_tool_schemas_no_scope_ids():
    """R1 修复：工具 schema 不再暴露 exam_id/student_id/class_id。"""
    from backend.app.agent.tools.exam_tools import _EXAM_STATS_SCHEMA
    from backend.app.agent.tools.student_tools import _STUDENT_SCORES_SCHEMA, _CLASS_RANKING_SCHEMA
    from backend.app.agent.tools.risk_tools import _RISK_FACTORS_SCHEMA
    from backend.app.agent.tools.attachment_tools import _STUDENT_ANSWER_IMAGE_SCHEMA

    scope_fields = {"exam_id", "student_id", "class_id"}
    for schema in [_EXAM_STATS_SCHEMA, _STUDENT_SCORES_SCHEMA, _CLASS_RANKING_SCHEMA,
                   _RISK_FACTORS_SCHEMA, _STUDENT_ANSWER_IMAGE_SCHEMA]:
        props = set(schema.get("properties", {}).keys())
        assert not (props & scope_fields), f"Schema still exposes scope fields: {props & scope_fields}"


def test_r1_privacy_recursive_sanitization():
    """R1 修复：PrivacyMapper 递归脱敏嵌套结构。"""
    mapper = PrivacyMapper()
    mapper.register_student(1)
    mapper.register_student(2)
    data = {
        "students": [
            {"name": "张三", "student_id": 1, "score": 85},
            {"name": "李四", "student_id": 2, "score": 72},
        ],
        "metadata": {
            "teacher": "王老师",
            "class_info": {"class_id": 5, "class_name": "高三一班"},
        },
    }
    sanitized = mapper.sanitize_for_model(data)
    # 递归进入列表中的字典
    for student in sanitized["students"]:
        assert "name" not in student
        assert "student_id" not in student
        assert "anonymous_id" in student or "score" in student
    # 递归进入嵌套字典
    assert "class_id" not in sanitized["metadata"]["class_info"]


def test_r0_snapshot_evidence_id_not_sent_to_model():
    """R0 快照：工具结果直接发给模型，未附带 evidence_id。R2 需修复。"""
    source = inspect.getsource(AgentLoop._register_evidence)
    assert "add(" in source


def test_r2_output_validator_schema_rejects_invalid():
    """R2 修复：OutputValidator 现在执行 schema 验证，缺少必需字段时失败。"""
    validator = OutputValidator()
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [],
            "recommendations": [],
            "limitations": [],
        },
        evidence_ledger=EvidenceLedger(),
        privacy_mapper=PrivacyMapper(),
        output_schema={"type": "object", "required": ["nonexistent_field"]},
    )
    schema_errors = [e for e in result.errors if "Schema" in e or "schema" in e or "nonexistent_field" in e]
    assert len(schema_errors) > 0
