"""P0-4 回归测试：修复验证失败后的修复重试逻辑

验证：
1. 修复调用必须是 tools-disabled（tools=None）
2. 修复调用必须是 temperature=0
3. 修复调用只执行一次，不重新执行工具循环
4. 初次调用与修复调用共享同一个硬预算
5. 两次 usage/request ID 均被完整保存
"""

from __future__ import annotations

import pytest

from backend.app.agent.config import AgentConfig
from backend.app.agent.context import TeachingContext
from backend.app.agent.evidence import EvidenceLedger
from backend.app.agent.loop import AgentLoop
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.registry.tools import ToolRegistry, ToolDefinition
from backend.app.agent.providers.base import ToolCall
from backend.tests.conftest import MockTextProvider


def _make_simple_registry() -> ToolRegistry:
    """创建只含一个工具的最小注册表。"""
    registry = ToolRegistry()

    def handler(**kwargs):
        return {"data": {"average_score": 75.5, "pass_rate": 0.8, "participant_count": 30}}

    registry.register(ToolDefinition(
        name="get_exam_statistics",
        description="获取考试统计",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=handler,
        category="exam",
        requires_scope=["exam_id"],
    ))
    return registry


def _make_context() -> TeachingContext:
    mapper = PrivacyMapper()
    mapper.register_student(1)
    return TeachingContext(
        scope={"exam_id": 1, "class_id": 1, "student_id": 1},
        capability="exam_analysis",
        available_tools=["get_exam_statistics"],
        privacy_mapper=mapper,
        evidence_ledger=EvidenceLedger(),
        budget_limit_yuan=0.5,
    )


@pytest.mark.asyncio
async def test_p04_repair_call_is_tools_disabled_and_temp_zero():
    """修复调用必须 tools=None, temperature=0。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_simple_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    # 设置修复响应
    provider.set_response(
        content='{"answer_type":"analysis","summary":"修复后的摘要","findings":[],"recommendations":[],"limitations":[]}',
        finish_reason="stop",
    )

    result = await loop.repair_call(
        system_prompt="你是教学分析助手",
        user_message="分析这次考试",
        invalid_answer="无效的原始输出",
        validation_errors=["缺少 summary 字段"],
        evidence_ids=["ev-001"],
        context=context,
        budget_limit_yuan=0.5,
        cost_already_spent=0.1,
    )

    assert result.success is True
    assert provider.call_count == 1
    call = provider.call_log[0]
    assert call["tools_disabled"] is True, "修复调用必须禁用工具 (tools=None)"
    assert call["temperature"] == 0.0, "修复调用必须 temperature=0"


@pytest.mark.asyncio
async def test_p04_repair_call_does_not_execute_tools():
    """修复调用不执行工具循环——只有一次 provider.complete() 调用。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_simple_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    provider.set_response(
        content='{"answer_type":"analysis","summary":"修复","findings":[],"recommendations":[],"limitations":[]}',
    )

    await loop.repair_call(
        system_prompt="你是教学分析助手",
        user_message="分析",
        invalid_answer="无效",
        validation_errors=["错误"],
        evidence_ids=[],
        context=context,
        budget_limit_yuan=0.5,
        cost_already_spent=0.0,
    )

    assert provider.call_count == 1, "修复调用只应有一次 LLM 调用，不应执行工具循环"


@pytest.mark.asyncio
async def test_p04_repair_call_shares_budget():
    """修复调用与初次调用共享同一硬预算。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_simple_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    # 预算已被初次调用消耗 0.49 元，剩余 0.01 元
    # 设置一个会产生少量 token 的响应
    provider.set_response(content="修复结果", input_tokens=10, output_tokens=5)

    result = await loop.repair_call(
        system_prompt="系统提示",
        user_message="用户消息",
        invalid_answer="无效",
        validation_errors=["错误"],
        evidence_ids=[],
        context=context,
        budget_limit_yuan=0.5,
        cost_already_spent=0.49,
    )

    # 即使成功调用，也不应超出总预算
    assert result.success is True
    total_spent = 0.49 + result.total_cost_yuan
    assert total_spent <= 0.5 + 0.001, f"总花费 {total_spent} 超过硬预算 0.5"


@pytest.mark.asyncio
async def test_p04_repair_call_budget_exhausted():
    """预算耗尽时修复调用应直接跳过。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_simple_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    result = await loop.repair_call(
        system_prompt="系统提示",
        user_message="用户消息",
        invalid_answer="无效",
        validation_errors=["错误"],
        evidence_ids=[],
        context=context,
        budget_limit_yuan=0.5,
        cost_already_spent=0.5,  # 已用完
    )

    assert result.success is False
    assert result.stop_reason == "budget_exceeded"
    assert provider.call_count == 0, "预算耗尽时不应调用模型"


@pytest.mark.asyncio
async def test_p04_repair_call_preserves_request_id():
    """修复调用必须保存 provider request ID。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_simple_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    provider.set_response(content="修复结果")

    result = await loop.repair_call(
        system_prompt="系统提示",
        user_message="用户消息",
        invalid_answer="无效",
        validation_errors=["错误"],
        evidence_ids=[],
        context=context,
        budget_limit_yuan=0.5,
        cost_already_spent=0.0,
    )

    assert result.success is True
    assert len(result.request_ids) == 1
    assert result.request_ids[0].startswith("mock-")


@pytest.mark.asyncio
async def test_p04_run_preserves_request_ids():
    """AgentLoop.run() 必须保存所有 LLM 调用的 request ID。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_simple_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    # 第一次响应：工具调用
    provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    # 第二次响应：最终答案
    provider.set_response(
        content='{"answer_type":"analysis","summary":"测试","findings":[],"recommendations":[],"limitations":[]}',
    )

    result = await loop.run(
        system_prompt="系统提示",
        user_message="分析考试",
        context=context,
        tools=[{
            "type": "function",
            "function": {
                "name": "get_exam_statistics",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }],
        max_iterations=2,
        budget_limit_yuan=0.5,
    )

    assert result.success is True
    assert len(result.request_ids) >= 1, "run() 必须保存至少一个 request ID"
    for rid in result.request_ids:
        assert rid.startswith("mock-")
