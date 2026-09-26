"""P0-7 回归测试：多轮上下文 build_multi_turn_messages() 接入编排链路

验证：
1. AgentLoop.run() 接受预构建 messages 参数
2. 当提供 messages 时，loop 使用这些消息而非默认 system+user
3. SessionService.build_multi_turn_messages() 包含历史消息
4. OrchestratorRequest 携带 db_session_id
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.agent.config import AgentConfig
from backend.app.agent.context import TeachingContext
from backend.app.agent.evidence import EvidenceLedger
from backend.app.agent.loop import AgentLoop
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.providers.base import ToolCall
from backend.app.agent.registry.tools import ToolRegistry, ToolDefinition
from backend.app.agent.orchestrator import OrchestratorRequest
from backend.app.models.agent_entities import AnalysisRun, AgentSession, AgentMessage
from backend.app.services.agent_analysis.sessions import SessionService
from backend.tests.conftest import MockTextProvider


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def handler(**kwargs):
        return {"data": {"average_score": 75.5}}

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
async def test_p07_loop_uses_prebuilt_messages():
    """AgentLoop.run() 使用预构建的 messages 而非默认 system+user。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
        reasoning_content="DeepSeek thinking trace",
    )
    provider.set_response(content='{"answer_type":"analysis","summary":"多轮测试"}')

    # 提供预构建的多轮消息
    prebuilt_messages = [
        {"role": "system", "content": "系统提示（含历史上下文）"},
        {"role": "user", "content": "之前的提问"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": "本次提问"},
    ]

    result = await loop.run(
        system_prompt="应被忽略的系统提示",
        user_message="应被忽略的用户消息",
        context=context,
        tools=_TOOLS,
        max_iterations=2,
        messages=prebuilt_messages,
    )

    assert result.success is True
    # 验证 provider 收到的第一条消息是预构建的系统提示
    first_call = provider.call_log[0]
    assert first_call["messages_count"] >= 4  # 4 条预构建 + 工具调用后的追加
    second_call_messages = provider.call_log[1]["messages"]
    assistant_turn = next(item for item in second_call_messages if item.get("role") == "assistant" and item.get("tool_calls"))
    assert assistant_turn["reasoning_content"] == "DeepSeek thinking trace"


@pytest.mark.asyncio
async def test_p07_loop_without_messages_uses_default():
    """不提供 messages 时，loop 使用默认 system+user 构建。"""
    provider = MockTextProvider()
    config = AgentConfig()
    registry = _make_registry()
    loop = AgentLoop(provider, registry, config)
    context = _make_context()

    provider.set_response(
        content="",
        tool_calls=[ToolCall(tool_name="get_exam_statistics", arguments={})],
        finish_reason="tool_calls",
    )
    provider.set_response(content='{"answer_type":"analysis","summary":"默认测试"}')

    result = await loop.run(
        system_prompt="系统提示",
        user_message="用户消息",
        context=context,
        tools=_TOOLS,
        max_iterations=2,
        # 不提供 messages，使用默认
    )

    assert result.success is True
    # 默认只有 2 条消息
    first_call = provider.call_log[0]
    assert first_call["messages_count"] == 2


def test_p07_orchestrator_request_has_db_session_id():
    """OrchestratorRequest 包含 db_session_id 字段。"""
    req = OrchestratorRequest(
        teacher_id=1,
        capability_name="exam_analysis",
        scope={"exam_id": 1},
        user_message="分析",
        db_session_id=42,
    )
    assert req.db_session_id == 42

    # 默认值 None
    req2 = OrchestratorRequest(
        teacher_id=1,
        capability_name="exam_analysis",
        scope={"exam_id": 1},
        user_message="分析",
    )
    assert req2.db_session_id is None


def test_p07_build_multi_turn_messages_includes_history():
    """SessionService.build_multi_turn_messages() 包含历史消息。"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)

    try:
        # 创建会话和消息
        agent_session = AgentSession(
            title="测试会话", term_id=1, exam_id=1, status="active",
        )
        session.add(agent_session)
        session.commit()

        # 添加历史消息
        msg1 = AgentMessage(session_id=agent_session.id, role="user", content_text="第一次提问")
        msg2 = AgentMessage(session_id=agent_session.id, role="assistant", content_text="第一次回答")
        session.add_all([msg1, msg2])
        session.commit()

        svc = SessionService(session)
        messages = svc.build_multi_turn_messages(
            session_id=agent_session.id,
            system_prompt="系统提示",
            user_message="第二次提问",
        )

        # 应包含：system + 历史 user + 历史 assistant + 新 user
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert roles.count("user") >= 2  # 历史 user + 新 user
        assert "assistant" in roles

        # 最后一条应该是新用户消息
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "第二次提问"
    finally:
        session.close()
        engine.dispose()


def test_current_run_message_is_not_duplicated_and_history_is_latest():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    try:
        agent_session = AgentSession(
            title="预算测试", term_id=1, exam_id=1, status="active",
        )
        session.add(agent_session)
        session.commit()
        for i in range(7):
            session.add(AgentMessage(
                session_id=agent_session.id, role="user", content_text=f"历史{i}",
            ))
        run = AnalysisRun(
            session_id=agent_session.id, term_id=1,
            capability="exam_analysis", status="running",
        )
        session.add(run)
        session.flush()
        session.add(AgentMessage(
            session_id=agent_session.id, analysis_run_id=run.id,
            role="user", content_text="本轮问题",
        ))
        session.commit()

        messages = SessionService(session).build_multi_turn_messages(
            session_id=agent_session.id,
            system_prompt="系统",
            user_message="本轮问题",
            current_run_id=run.id,
        )
        contents = [m["content"] for m in messages]
        assert contents.count("本轮问题") == 1
        assert "历史0" not in [m["content"] for m in messages if m["role"] == "user"]
        assert "历史1" in contents
        assert "历史6" in contents
        summary = next(
            m["content"] for m in messages
            if m["role"] == "system" and "自动滚动摘要" in m["content"]
        )
        assert "历史0" in summary
    finally:
        session.close()
        engine.dispose()


def test_rolling_summary_preserves_manual_summary_and_caps_raw_history():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    try:
        agent_session = AgentSession(
            title="摘要测试", term_id=1, status="active",
            summary="教师偏好：输出简洁表格。",
        )
        session.add(agent_session)
        session.commit()
        for i in range(10):
            session.add(AgentMessage(
                session_id=agent_session.id,
                role="user" if i % 2 == 0 else "assistant",
                content_text=f"第{i}条历史消息",
                structured_answer_json={"summary": f"第{i}条结论"} if i % 2 else {},
            ))
        session.commit()

        messages = SessionService(session).build_multi_turn_messages(
            session_id=agent_session.id,
            system_prompt="系统",
            user_message="当前问题",
            include_formal_context=False,
        )
        raw_history = [
            item for item in messages[1:-1]
            if item["role"] in {"user", "assistant"}
        ]
        assert len(raw_history) == 6
        assert any("第9条结论" in item["content"] for item in raw_history)
        assert not any("第9条历史消息" in item["content"] for item in raw_history)
        summary = next(
            item["content"] for item in messages
            if item["role"] == "system" and "会话摘要" in item["content"]
        )
        assert "教师偏好：输出简洁表格。" in summary
        assert "[自动滚动摘要 v1]" in summary
        assert "第0条历史消息" in summary
        assert "第1条结论" in summary
    finally:
        session.close()
        engine.dispose()


def test_history_token_budget_prefers_newest_messages():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    try:
        agent_session = AgentSession(title="预算顺序", term_id=1, status="active")
        session.add(agent_session)
        session.commit()
        session.add_all([
            AgentMessage(
                session_id=agent_session.id,
                role="assistant",
                content_text="旧回答" * 500,
            ),
            AgentMessage(
                session_id=agent_session.id,
                role="user",
                content_text="最近追问",
            ),
        ])
        session.commit()

        messages = SessionService(session).build_multi_turn_messages(
            session_id=agent_session.id,
            system_prompt="系统",
            user_message="当前问题",
            history_token_budget=100,
            include_formal_context=False,
        )
        contents = [item["content"] for item in messages]
        assert "最近追问" in contents
        assert not any(content.startswith("旧回答") for content in contents)
    finally:
        session.close()
        engine.dispose()
