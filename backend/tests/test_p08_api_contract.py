"""P0-8 回归测试：Agent 消息 API 契约对齐

验证：
1. AgentMessageRead 使用 content_text / structured_answer / evidence_ids 字段名
2. 从 ORM 对象映射时，structured_answer_json -> structured_answer, evidence_ids_json -> evidence_ids
3. API JSON 响应中不暴露 ORM 字段名 (structured_answer_json, evidence_ids_json)
4. structured_answer 和 evidence_ids 已是对象/数组，前端无需 JSON.parse()
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.models.agent_entities import AgentMessage, AgentSession, AnalysisRun
from backend.app.routers.agent import list_messages
from backend.app.schemas.agent import AgentMessageRead, AnalysisRunRead


def test_run_read_exposes_non_sensitive_phase_timings():
    payload = {
        "id": 1, "capability": "general_chat", "status": "completed",
        "created_at": datetime.now(timezone.utc),
        "input_summary_json": {"timings_ms": {"queue": 8, "model_call": 123}},
    }
    assert AnalysisRunRead.model_validate(payload).timings_ms == {
        "queue": 8, "model_call": 123,
    }


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _create_message(db_session, **kwargs):
    """创建并提交一条 AgentMessage，返回刷新后的对象。"""
    agent_session = AgentSession(title="test", term_id=1, status="active")
    db_session.add(agent_session)
    db_session.commit()

    kwargs.setdefault("role", "assistant")
    msg = AgentMessage(session_id=agent_session.id, **kwargs)
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


def test_p08_message_read_uses_correct_field_names(db_session):
    """AgentMessageRead 使用 content_text / structured_answer / evidence_ids。"""
    msg = _create_message(db_session,
        content_text="分析结果文本",
        structured_answer_json={"answer_type": "analysis", "summary": "测试摘要"},
        evidence_ids_json=["ev-001", "ev-002"],
        model_name="deepseek-chat",
        provider_request_id="req-001",
    )

    read = AgentMessageRead.model_validate(msg)

    assert read.content_text == "分析结果文本"
    assert read.structured_answer == {"answer_type": "analysis", "summary": "测试摘要"}
    assert read.evidence_ids == ["ev-001", "ev-002"]
    assert read.model_name == "deepseek-chat"
    assert read.provider_request_id == "req-001"


def test_p08_message_read_no_orm_field_names_in_json(db_session):
    """序列化后的 JSON 不包含 ORM 字段名。"""
    msg = _create_message(db_session,
        content_text="测试",
        structured_answer_json={"key": "value"},
        evidence_ids_json=["ev-001"],
    )

    read = AgentMessageRead.model_validate(msg)
    json_data = read.model_dump()

    # 不应包含 ORM 字段名
    assert "structured_answer_json" not in json_data
    assert "evidence_ids_json" not in json_data

    # 应包含固定字段名
    assert "structured_answer" in json_data
    assert "evidence_ids" in json_data
    assert "content_text" in json_data


def test_p08_structured_answer_is_object_not_string(db_session):
    """structured_answer 已是对象，不需要 JSON.parse()。"""
    msg = _create_message(db_session,
        content_text="测试",
        structured_answer_json={"answer_type": "analysis", "summary": "测试"},
        evidence_ids_json=["ev-001"],
    )

    read = AgentMessageRead.model_validate(msg)

    # structured_answer 应该是 dict，不是 str
    assert isinstance(read.structured_answer, dict)
    assert read.structured_answer["answer_type"] == "analysis"

    # evidence_ids 应该是 list，不是 str
    assert isinstance(read.evidence_ids, list)
    assert read.evidence_ids[0] == "ev-001"


def test_p08_message_read_from_dict_mapping():
    """从字典（如 API 响应）构建时也能正确映射。"""
    data = {
        "id": 1,
        "role": "user",
        "content_text": "用户提问",
        "structured_answer_json": {"key": "value"},
        "evidence_ids_json": ["ev-001"],
        "created_at": datetime.now(timezone.utc),
    }

    read = AgentMessageRead.model_validate(data)

    assert read.content_text == "用户提问"
    assert read.structured_answer == {"key": "value"}
    assert read.evidence_ids == ["ev-001"]


def test_p08_message_read_empty_structured_answer(db_session):
    """空 structured_answer_json 映射为 None。"""
    msg = _create_message(db_session,
        role="user",
        content_text="用户消息",
        structured_answer_json={},
        evidence_ids_json=[],
    )

    read = AgentMessageRead.model_validate(msg)

    assert read.structured_answer is None
    assert read.evidence_ids == []


def test_message_history_exposes_capability_for_plugin_badge(db_session):
    """重新打开历史对话后，用户消息仍能恢复本次调用的插件标识。"""
    agent_session = AgentSession(title="插件会话", term_id=1, status="active")
    db_session.add(agent_session)
    db_session.flush()
    run = AnalysisRun(
        session_id=agent_session.id,
        capability="exam_analysis",
        term_id=1,
        status="completed",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(AgentMessage(
        session_id=agent_session.id,
        analysis_run_id=run.id,
        role="user",
        content_text="帮我分析考试情况",
    ))
    db_session.commit()

    messages = list_messages(agent_session.id, session=db_session)

    assert len(messages) == 1
    assert messages[0].capability == "exam_analysis"


def test_batch_child_reports_are_hidden_from_chat_history(db_session):
    """批量子运行报告保留在库中写画像，但聊天历史只返回教师消息。"""
    agent_session = AgentSession(title="批量诊断", term_id=1, class_id=7, status="active")
    db_session.add(agent_session)
    db_session.flush()
    batch_run = AnalysisRun(
        session_id=agent_session.id,
        capability="student_diagnosis",
        term_id=1,
        class_id=7,
        student_id=42,
        status="completed",
        input_summary_json={"analysis_group_id": 99},
    )
    db_session.add(batch_run)
    db_session.flush()
    db_session.add_all([
        AgentMessage(session_id=agent_session.id, role="user", content_text="给全班做学生画像"),
        AgentMessage(
            session_id=agent_session.id,
            analysis_run_id=batch_run.id,
            role="assistant",
            content_text="张三的具体诊断报告",
            structured_answer_json={"profile_summary": "画像已写入"},
        ),
    ])
    db_session.commit()

    messages = list_messages(agent_session.id, session=db_session)

    assert [message.content_text for message in messages] == ["给全班做学生画像"]
