"""P0-5 回归测试：强制至少一次成功工具调用和一条证据

验证：
1. 模型未调用任何工具就给出回答 → success=False, stop_reason="data_insufficient"
2. 模型调用工具后给出回答 → success=True, stop_reason="completed"
3. 有证据但没有结构化答案 → 仍可成功（验证器会处理结构）
"""

from __future__ import annotations

import pytest

from backend.app.agent.config import AgentConfig
from backend.app.agent.context import TeachingContext
from backend.app.agent.evidence import EvidenceLedger
from backend.app.agent.loop import AgentLoop
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.providers.base import ToolCall
from backend.app.agent.registry.tools import ToolRegistry, ToolDefinition
from backend.tests.conftest import MockTextProvider


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def handler(**kwargs):
        return {"data": {"average_score": 75.5, "participant_count": 30}}

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


_TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_exam_statistics",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]


@pytest.mark.asyncio
async def test_p05_no_tool_call_no_evidence_returns_data_insufficient():
    """模型未调用工具直接回答 → data_insufficient。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    # 模型直接给出答案，不调用工具
    provider.set_response(
        content='{"answer_type":"analysis","summary":"没有数据支撑的分析"}',
    )

    result = await loop.run(
        system_prompt="系统提示",
        user_message="分析考试",
        context=context,
        tools=_TOOLS,
        max_iterations=2,
    )

    assert result.success is False
    assert result.stop_reason == "data_insufficient"
    assert len(context.evidence_ledger.all_ids()) == 0


@pytest.mark.asyncio
async def test_p05_with_tool_call_and_evidence_returns_success():
    """模型调用工具后再回答 → success。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_registry()
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
        content='{"answer_type":"analysis","summary":"有数据支撑的分析"}',
    )

    result = await loop.run(
        system_prompt="系统提示",
        user_message="分析考试",
        context=context,
        tools=_TOOLS,
        max_iterations=2,
    )

    assert result.success is True
    assert result.stop_reason == "completed"
    assert len(context.evidence_ledger.all_ids()) >= 1


@pytest.mark.asyncio
async def test_p05_no_tool_call_on_second_iteration_with_evidence_from_first():
    """第一次迭代调用工具，第二次不调用 → 仍应成功（有证据）。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    # 第一次响应：工具调用
    provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    # 第二次响应：不调用工具，给出最终答案
    provider.set_response(
        content='{"answer_type":"analysis","summary":"基于证据的分析"}',
    )

    result = await loop.run(
        system_prompt="系统提示",
        user_message="分析考试",
        context=context,
        tools=_TOOLS,
        max_iterations=3,
    )

    assert result.success is True
    assert result.stop_reason == "completed"
    assert len(context.evidence_ledger.all_ids()) >= 1


@pytest.mark.asyncio
async def test_general_chat_allows_direct_answer_without_evidence():
    """普通对话不调用工具时应直接成功。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = ToolRegistry()
    loop = AgentLoop(provider, registry, config)
    context = TeachingContext(
        scope={},
        capability="general_chat",
        available_tools=[],
        privacy_mapper=PrivacyMapper(),
        evidence_ledger=EvidenceLedger(),
        budget_limit_yuan=0.5,
    )
    provider.set_response(content="你好，我可以帮你讨论教学问题。")

    result = await loop.run(
        system_prompt="普通对话",
        user_message="你好",
        context=context,
        tools=[],
        max_iterations=1,
        require_evidence=False,
    )

    assert result.success is True
    assert result.stop_reason == "completed"
    assert result.final_answer == "你好，我可以帮你讨论教学问题。"
