"""P0-6 回归测试：持久化 EvidenceLedger/工具轨迹/验证结果/usage

验证：
1. OrchestratorResponse 携带 steps（工具轨迹）和 request_ids
2. AnalysisEvidence 唯一键为 (run_id, evidence_id)
3. LlmUsageRecord 可以绑定到 run 并保存 request_id
4. AnalysisRun 可以存储工具轨迹和验证结果
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.agent.orchestrator import OrchestratorResponse
from backend.app.agent.loop import LoopStep
from backend.app.models.agent_entities import (
    AnalysisRun,
    AnalysisEvidence,
    LlmUsageRecord,
)


@pytest.fixture
def db_session():
    """内存 SQLite 数据库 session。"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_p06_orchestrator_response_has_steps_and_request_ids():
    """OrchestratorResponse 必须包含 steps 和 request_ids 字段。"""
    resp = OrchestratorResponse(
        success=True,
        session_id="test-session",
        answer="测试回答",
        request_ids=["req-001", "req-002"],
        steps=[LoopStep(iteration=1)],
    )
    assert resp.request_ids == ["req-001", "req-002"]
    assert len(resp.steps) == 1
    assert resp.steps[0].iteration == 1


def test_p06_analysis_evidence_unique_key_is_run_eid(db_session):
    """AnalysisEvidence 唯一键应为 (run_id, evidence_id)，允许不同 run 复用同一 eid。"""
    run1 = AnalysisRun(
        session_id=None, capability="exam_analysis", term_id=1,
        status="completed",
    )
    run2 = AnalysisRun(
        session_id=None, capability="exam_analysis", term_id=1,
        status="completed",
    )
    db_session.add_all([run1, run2])
    db_session.commit()

    # 同一 evidence_id 在不同 run 下可以存在
    ev1 = AnalysisEvidence(
        evidence_id="ev-001", run_id=run1.id,
        evidence_type="db_metric", local_fact_json={},
    )
    ev2 = AnalysisEvidence(
        evidence_id="ev-001", run_id=run2.id,
        evidence_type="db_metric", local_fact_json={},
    )
    db_session.add_all([ev1, ev2])
    db_session.commit()

    assert ev1.id != ev2.id
    assert ev1.evidence_id == ev2.evidence_id
    assert ev1.run_id != ev2.run_id


def test_p06_llm_usage_record_persisted(db_session):
    """LlmUsageRecord 可以绑定到 run 并保存 request_id。"""
    run = AnalysisRun(
        session_id=None, capability="exam_analysis", term_id=1,
        status="completed",
    )
    db_session.add(run)
    db_session.commit()

    usage = LlmUsageRecord(
        run_id=run.id,
        provider="deepseek",
        model_name="deepseek-chat",
        stage="text_analysis",
        input_tokens=500,
        output_tokens=1000,
        cost_yuan=0.01,
        provider_request_id="test-req-001",
    )
    db_session.add(usage)
    db_session.commit()

    assert usage.id is not None
    assert usage.run_id == run.id
    assert usage.provider_request_id == "test-req-001"


def test_p06_analysis_run_stores_tool_calls_and_validation(db_session):
    """AnalysisRun.tool_calls_json 和 input_summary_json 可以存储工具轨迹和验证结果。"""
    run = AnalysisRun(
        session_id=None, capability="exam_analysis", term_id=1,
        status="completed",
        tool_calls_json=[
            {"name": "get_exam_statistics", "arguments": {}},
        ],
        input_summary_json={
            "stop_reason": "completed",
            "validation_valid": True,
            "validation_degraded": False,
            "validation_errors": [],
            "request_ids": ["req-001"],
        },
        actual_cost_yuan=0.05,
        actual_tokens=1500,
    )
    db_session.add(run)
    db_session.commit()

    result = db_session.scalar(select(AnalysisRun).where(AnalysisRun.id == run.id))
    assert result is not None
    assert len(result.tool_calls_json) == 1
    assert result.tool_calls_json[0]["name"] == "get_exam_statistics"
    assert result.input_summary_json["stop_reason"] == "completed"
    assert result.input_summary_json["validation_valid"] is True
    assert result.input_summary_json["request_ids"] == ["req-001"]
