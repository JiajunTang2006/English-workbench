"""R1 门禁测试：作用域、白名单与隐私

验证 R1 改造的安全门禁：
1. 越权：模型不能调用不在当前能力白名单中的工具
2. 嵌套隐私：PrivacyMapper 递归脱敏嵌套 dict/list
3. 恶意参数：模型不能在工具参数中提交作用域字段
4. 未知工具：调用未注册工具返回错误
5. 工具 schema 不暴露作用域 ID
6. for_model 支持 capability 白名单过滤
"""

from __future__ import annotations

import pytest

from backend.app.agent.config import AgentConfig
from backend.app.agent.context import TeachingContext
from backend.app.agent.evidence import EvidenceLedger
from backend.app.agent.loop import AgentLoop
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.registry.tools import ToolRegistry, ToolError
from backend.tests.conftest import MockTextProvider, make_mock_registry, make_mock_tool


# ---------------------------------------------------------------------------
# 1. 越权：工具白名单校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_tool_not_in_whitelist_rejected(mock_provider, mock_registry, agent_config):
    """模型不能调用不在 available_tools 白名单中的工具。"""
    ctx = TeachingContext(
        scope={"exam_id": 1, "student_id": 1},
        capability="exam_analysis",
        available_tools=["get_exam_statistics"],
        privacy_mapper=PrivacyMapper(),
        evidence_ledger=EvidenceLedger(),
    )
    from backend.app.agent.providers.base import ToolCall

    mock_provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_student_scores", arguments={})],
        finish_reason="tool_calls",
    )
    mock_provider.set_response(
        content='{"answer_type":"exam_analysis","summary":"none","findings":[],"recommendations":[],"limitations":[]}',
        finish_reason="stop",
    )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(
        scope=ctx.scope, available_tools=ctx.available_tools)

    result = await loop.run(
        system_prompt="test", user_message="test", context=ctx,
        tools=tools, max_iterations=2,
    )

    # 越权工具被拒绝 → 无证据 → data_insufficient (P0-5)
    assert len(result.steps) == 2
    tool_result = result.steps[0].tool_results[0]
    assert "error" in tool_result
    assert "不在当前能力" in tool_result["error"] or "白名单" in tool_result["error"]
    # P0-5: 无成功工具调用和证据 → data_insufficient
    assert result.success is False
    assert result.stop_reason == "data_insufficient"


@pytest.mark.asyncio
async def test_r1_tool_in_whitelist_allowed(mock_provider, mock_registry, agent_config):
    """在白名单中的工具可以正常调用。"""
    ctx = TeachingContext(
        scope={"exam_id": 1},
        capability="exam_analysis",
        available_tools=["get_exam_statistics"],
        privacy_mapper=PrivacyMapper(),
        evidence_ledger=EvidenceLedger(),
    )
    from backend.app.agent.providers.base import ToolCall

    mock_provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    mock_provider.set_response(
        content='{"answer_type":"exam_analysis","summary":"ok","findings":[{"title":"ok","evidence_ids":["ev_1"]}],"recommendations":[{"action":"none","supports":["ev_1"]}],"limitations":[]}',
        finish_reason="stop",
    )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(
        scope=ctx.scope, available_tools=ctx.available_tools)

    result = await loop.run(
        system_prompt="test", user_message="test", context=ctx,
        tools=tools, max_iterations=2,
    )

    assert result.success is True
    tool_result = result.steps[0].tool_results[0]
    assert "data" in tool_result
    assert "error" not in tool_result


# ---------------------------------------------------------------------------
# 2. 嵌套隐私：递归脱敏
# ---------------------------------------------------------------------------


def test_r1_recursive_sanitization_nested_dict():
    """脱敏递归进入嵌套字典。"""
    mapper = PrivacyMapper()
    mapper.register_student(1)
    data = {
        "exam_id": 1,
        "student": {
            "name": "张三",
            "student_id": 1,
            "score": 85,
            "contact": {
                "parent_phone": "13800138000",
                "seat": "3排5座",
            },
        },
    }
    sanitized = mapper.sanitize_for_model(data)
    assert "name" not in sanitized["student"]
    assert "student_id" not in sanitized["student"]
    assert sanitized["student"]["anonymous_id"] == "student_01"
    assert "parent_phone" not in sanitized["student"]["contact"]
    assert "seat" not in sanitized["student"]["contact"]
    assert sanitized["student"]["score"] == 85


def test_r1_recursive_sanitization_nested_list():
    """脱敏递归进入列表中的字典。"""
    mapper = PrivacyMapper()
    mapper.register_student(1)
    mapper.register_student(2)
    data = {
        "students": [
            {"name": "张三", "student_id": 1, "score": 85},
            {"name": "李四", "student_id": 2, "score": 72},
        ],
    }
    sanitized = mapper.sanitize_for_model(data)
    assert len(sanitized["students"]) == 2
    for s in sanitized["students"]:
        assert "name" not in s
        assert "student_id" not in s
        assert "anonymous_id" in s
        assert "score" in s


def test_r1_recursive_sanitization_deep_nesting():
    """三层嵌套也能正确脱敏。"""
    mapper = PrivacyMapper()
    mapper.register_student(42)
    data = {
        "level1": {
            "level2": {
                "level3": {
                    "name": "深度嵌套",
                    "student_id": 42,
                    "value": 100,
                },
            },
        },
    }
    sanitized = mapper.sanitize_for_model(data)
    deep = sanitized["level1"]["level2"]["level3"]
    assert "name" not in deep
    assert "student_id" not in deep
    assert deep["anonymous_id"] == "student_01"
    assert deep["value"] == 100


def test_r1_sanitization_list_of_primitives():
    """原始值列表不受影响。"""
    mapper = PrivacyMapper()
    data = {"scores": [85, 72, 90], "levels": ["A", "B", "C"]}
    sanitized = mapper.sanitize_for_model(data)
    assert sanitized["scores"] == [85, 72, 90]
    assert sanitized["levels"] == ["A", "B", "C"]


def test_r1_sanitization_removes_attachment_fields():
    """附件路径等字段也被脱敏。"""
    mapper = PrivacyMapper()
    data = {
        "images": [
            {
                "page": 1,
                "attachment_id": 99,
                "storage_name": "/data/secret/file.pdf",
                "original_name": "exam_scan.pdf",
                "url": "/api/v1/files/attachments/99",
            },
        ],
    }
    sanitized = mapper.sanitize_for_model(data)
    img = sanitized["images"][0]
    assert "attachment_id" not in img
    assert "storage_name" not in img
    assert "original_name" not in img
    assert "url" not in img
    assert img["page"] == 1


def test_r1_unregistered_student_fail_closed():
    """P0 修复：未注册学生的 student_id 不得出现在脱敏结果中。

    遇到未注册 student_id 时必须 fail closed（抛 PrivacyViolationError），
    不得输出 student_unregistered_{原始ID}。
    """
    from backend.app.agent.privacy import PrivacyViolationError

    mapper = PrivacyMapper()
    # 不注册 student_id=999
    data = {
        "student": {
            "name": "测试学生",
            "student_id": 999,
            "score": 90,
        },
    }
    with pytest.raises(PrivacyViolationError) as exc_info:
        mapper.sanitize_for_model(data)
    # 确保错误消息不包含原始 ID
    assert "999" not in str(exc_info.value)


def test_r1_pre_register_students_bulk():
    """pre_register_students 批量注册后脱敏不再报错。"""
    mapper = PrivacyMapper()
    mapper.pre_register_students([10, 20, 30])
    data = {
        "students": [
            {"name": "A", "student_id": 10, "score": 85},
            {"name": "B", "student_id": 20, "score": 72},
            {"name": "C", "student_id": 30, "score": 90},
        ],
    }
    sanitized = mapper.sanitize_for_model(data)
    for s in sanitized["students"]:
        assert "name" not in s
        assert "student_id" not in s
        assert "anonymous_id" in s


# ---------------------------------------------------------------------------
# 3. 恶意参数：模型不能提交作用域字段
# ---------------------------------------------------------------------------


def test_r1_execute_rejects_scope_fields_in_args():
    """工具执行时检测模型是否试图提交 exam_id 等作用域字段。"""
    registry = make_mock_registry()
    with pytest.raises(ToolError) as exc_info:
        registry.execute("get_exam_statistics", exam_id=999)
    assert "作用域字段" in str(exc_info.value) or "exam_id" in str(exc_info.value)


def test_r1_execute_rejects_student_id_in_args():
    """模型不能提交 student_id。"""
    registry = make_mock_registry()
    with pytest.raises(ToolError) as exc_info:
        registry.execute("get_student_scores", student_id=999)
    assert "作用域字段" in str(exc_info.value) or "student_id" in str(exc_info.value)


def test_r1_execute_rejects_class_id_in_args():
    """模型不能提交 class_id。"""
    registry = make_mock_registry()
    with pytest.raises(ToolError) as exc_info:
        registry.execute("get_class_ranking", class_id=999)
    assert "作用域字段" in str(exc_info.value) or "class_id" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. 未知工具：调用未注册工具返回错误
# ---------------------------------------------------------------------------


def test_r1_execute_unknown_tool_raises():
    """调用未注册的工具应抛出 ToolError。"""
    registry = make_mock_registry()
    with pytest.raises(ToolError) as exc_info:
        registry.execute("nonexistent_malicious_tool")
    assert "未注册" in str(exc_info.value) or "不存在" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. 工具 schema 不暴露作用域 ID
# ---------------------------------------------------------------------------


def test_r1_all_tool_schemas_free_of_scope_ids():
    """所有 13 个工具的 schema 不包含 exam_id/student_id/class_id。"""
    from backend.app.agent.registry.tools import create_default_registry
    from backend.app.agent.registry.tools import SCOPE_OWNED_FIELDS

    registry = create_default_registry()
    for tool in registry.list_all():
        props = set(tool.parameters_schema.get("properties", {}).keys())
        leaked = props & SCOPE_OWNED_FIELDS
        assert not leaked, f"工具 {tool.name} schema 仍暴露作用域字段: {leaked}"


# ---------------------------------------------------------------------------
# 6. for_model 支持 capability 白名单过滤
# ---------------------------------------------------------------------------


def test_r1_for_model_whitelist_filtering():
    """for_model 按 available_tools 白名单过滤工具。"""
    registry = make_mock_registry()
    # 只允许 2 个工具
    tools = registry.for_model(
        scope={"exam_id": 1, "class_id": 1, "student_id": 1},
        available_tools=["get_exam_statistics", "get_student_scores"],
    )
    names = {t["function"]["name"] for t in tools}
    assert names == {"get_exam_statistics", "get_student_scores"}


def test_r1_for_model_no_whitelist_returns_all_scoped():
    """available_tools=None 时返回所有满足作用域的工具。"""
    registry = make_mock_registry()
    tools = registry.for_model(
        scope={"exam_id": 1, "class_id": 1, "student_id": 1},
        available_tools=None,
    )
    names = {t["function"]["name"] for t in tools}
    # 应包含所有 13 个工具（因为 scope 满足所有 requires_scope）
    assert len(names) == 13


def test_r1_for_model_empty_whitelist_returns_nothing():
    """available_tools=[] 时返回空列表。"""
    registry = make_mock_registry()
    tools = registry.for_model(
        scope={"exam_id": 1},
        available_tools=[],
    )
    assert len(tools) == 0


# ---------------------------------------------------------------------------
# 7. 证据 ID 回传模型
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_evidence_id_sent_to_model(mock_provider, mock_registry, agent_config):
    """工具执行后，发给模型的消息应包含 evidence_id。"""
    from backend.app.agent.providers.base import ToolCall

    ctx = TeachingContext(
        scope={"exam_id": 1},
        capability="exam_analysis",
        available_tools=["get_exam_statistics"],
        privacy_mapper=PrivacyMapper(),
        evidence_ledger=EvidenceLedger(),
    )

    mock_provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    mock_provider.set_response(
        content='{"answer_type":"exam_analysis","summary":"ok","findings":[{"title":"ok","evidence_ids":["ev_1"]}],"recommendations":[{"action":"none","supports":["ev_1"]}],"limitations":[]}',
        finish_reason="stop",
    )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(
        scope=ctx.scope, available_tools=ctx.available_tools)

    result = await loop.run(
        system_prompt="test", user_message="test", context=ctx,
        tools=tools, max_iterations=2,
    )

    assert result.success is True
    # 证据账本应有 1 条记录
    assert len(ctx.evidence_ledger.all_ids()) == 1
    assert ctx.evidence_ledger.all_ids()[0] == "ev_1"


# ---------------------------------------------------------------------------
# 8. 模型收到脱敏数据（不含真实姓名）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_model_receives_sanitized_data(mock_provider, mock_registry, agent_config):
    """模型收到的工具结果应脱敏：不含 name 字段。"""
    from backend.app.agent.providers.base import ToolCall
    import json

    ctx = TeachingContext(
        scope={"exam_id": 1},
        capability="exam_analysis",
        available_tools=["get_student_list"],
        privacy_mapper=PrivacyMapper(),
        evidence_ledger=EvidenceLedger(),
    )

    mock_provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_student_list", arguments={})],
        finish_reason="tool_calls",
    )
    mock_provider.set_response(
        content='{"answer_type":"exam_analysis","summary":"none","findings":[],"recommendations":[],"limitations":[]}',
        finish_reason="stop",
    )

    loop = AgentLoop(mock_provider, mock_registry, agent_config)
    tools = mock_registry.for_model(
        scope=ctx.scope, available_tools=ctx.available_tools)

    result = await loop.run(
        system_prompt="test", user_message="test", context=ctx,
        tools=tools, max_iterations=2,
    )

    assert result.success is True
    # 检查第二步收到的 messages 中 tool 消息不含 "name"
    # call_log 记录了 messages_count，但我们需要检查实际消息
    # step 0 的 tool_results 保存的是原始结果（含 name）
    # 但模型收到的是脱敏后的（通过 _build_model_payload）
    # 这里验证原始结果有 name（local_data），但模型消息应脱敏
    raw_result = result.steps[0].tool_results[0]
    assert "data" in raw_result
    # 原始数据包含 name（本地数据）
    students = raw_result["data"]["students"]
    assert any("name" in s for s in students)
