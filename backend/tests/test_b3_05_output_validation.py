"""B3-05 统一输出验证与持久化测试（harness 路径）

覆盖：
1. 模型伪造 evidence ID → 验证失败 → run 不标记 completed（降级而非成功保存）；
2. 引用其它 run 的证据 → 拒绝；
3. 输出含真实姓名/电话 → failed（不持久化任何 assistant 消息）；
4. 合格结构化报告（引用本 run evidence）→ completed 且保存；
5. 纯文本答案（非报告）→ completed；
6. 首次验证失败 → 一次低温修复；修复后通过 → completed；
   修复仍失败 → 本地确定性降级报告（degraded，仅含已验证 evidence 事实）；
7. 验证完成前不标记 completed（失败路径 run.status != completed）。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.entities import Term
from backend.app.models.agent_entities import (
    AgentSession, AnalysisRun, AgentMessage, AnalysisEvidence,
)


@pytest.fixture
def factory():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b305_")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
    os.unlink(path)


@pytest.fixture
def world(factory):
    with factory() as db:
        t = Term(code="t1", name="2026春", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        db.add(t)
        db.commit()
        agent = AgentSession(title="S1", term_id=t.id, status="active")
        db.add(agent)
        db.commit()
        run = AnalysisRun(session_id=agent.id, term_id=t.id,
                          capability="exam_analysis", status="queued")
        db.add(run)
        db.commit()
        return {"term_id": t.id, "agent_id": agent.id, "run_id": run.id}


class _FakeManager:
    """fake HarnessManager：按顺序返回回合结果（离线）。"""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls = []
        self._config = type("C", (), {"scope_root": ""})()
        self._outbound_observer = None

    async def call(self, method, **params):
        self.calls.append(params)
        if not self.responses:
            return {}
        return self.responses.pop(0)

    async def cancel_session(self, sid):
        return True


def _run_harness(factory, world, manager):
    from backend.app.agent.run_executor import _execute_harness_run_managed
    from backend.app.agent.orchestrator import OrchestratorRequest
    from backend.app.agent.task_registry import TaskRegistry
    import backend.app.agent.run_executor as re_mod

    # 强制隔离：内存语义真实，DB 持久化 noop（不受跨文件全局 factory 污染）
    # 注意：run_executor 函数内 `from ..agent.task_registry import get_task_registry`
    # 会绕过 run_executor 模块属性 patch，因此必须打底层模块属性。
    fresh = TaskRegistry()

    async def noop_persist(*a, **k):
        return None

    fresh._persist_event = noop_persist
    import backend.app.agent.task_registry as tr_mod
    tr_mod.get_task_registry = lambda: fresh
    re_mod.get_task_registry = lambda: fresh

    request = OrchestratorRequest(
        teacher_id=1, capability_name="exam_analysis",
        scope={"term_id": world["term_id"], "exam_id": 1},
        user_message="请分析考试", session_id=None, db_session_id=world["agent_id"],
    )
    asyncio.run(_execute_harness_run_managed(
        world["run_id"], world["agent_id"], request, factory, manager,
    ))


def _add_evidence(factory, world, eid: str):
    with factory() as db:
        db.add(AnalysisEvidence(evidence_id=eid, run_id=world["run_id"],
                                evidence_type="db_metric",
                                local_fact_json={"facts": [{"text": "平均80"}]},
                                source_entity="exam:1", display_summary="平均80",
                                contains_personal_data=False))
        db.commit()


def _report(eid: str) -> str:
    return json.dumps({
        "answer_type": "exam_analysis", "summary": "报告摘要",
        "findings": [{"title": "平均分", "detail": "80", "evidence_ids": [eid]}],
        "recommendations": [{"title": "建议", "action": "补强", "supports": [eid]}],
        "limitations": [], "scope_snapshot": {}, "schema_version": "1.0.0",
    }, ensure_ascii=False)


def _assistant_messages(factory, world):
    with factory() as db:
        return db.query(AgentMessage).filter_by(
            session_id=world["agent_id"], role="assistant").all()


def _run_status(factory, run_id):
    with factory() as db:
        return db.get(AnalysisRun, run_id).status


# ---------------------------------------------------------------------------

class TestValidationChain:

    def test_fake_evidence_not_completed(self, factory, world):
        # DB 无任何 evidence，模型引用 ev-fake
        m = _FakeManager([{"finalResponse": _report("ev-fake"), "finishReason": "stop"}])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) == "degraded"

    def test_cross_run_evidence_rejected(self, factory, world):
        # 其它 run（id=9999）的证据
        with factory() as db:
            db.add(AnalysisEvidence(evidence_id="ev-other", run_id=9999,
                                    evidence_type="db_metric",
                                    local_fact_json={"facts": []},
                                    source_entity="x", display_summary="x",
                                    contains_personal_data=False))
            db.commit()
        m = _FakeManager([{"finalResponse": _report("ev-other"), "finishReason": "stop"}])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) == "degraded"

    def test_privacy_leak_failed(self, factory, world):
        m = _FakeManager([{"finalResponse": "张三同学表现优异 13800000000",
                           "finishReason": "stop"}])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) == "failed"
        assert _assistant_messages(factory, world) == []

    def test_valid_report_completed_and_saved(self, factory, world):
        _add_evidence(factory, world, "ev-ok")
        m = _FakeManager([{"finalResponse": _report("ev-ok"), "finishReason": "stop"}])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) == "completed"
        msgs = _assistant_messages(factory, world)
        assert len(msgs) == 1
        assert msgs[0].structured_answer_json["findings"][0]["evidence_ids"] == ["ev-ok"]

    def test_plain_text_completed(self, factory, world):
        m = _FakeManager([{"finalResponse": "本次考试平均分80，合格率85%。",
                           "finishReason": "stop"}])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) == "completed"

    def test_empty_harness_text_uses_bridge_submitted_report(self, factory, world):
        """submit_report 成功后 Harness 直接 idle 也应完成运行。"""
        _add_evidence(factory, world, "ev-bridge")
        with factory() as db:
            run = db.get(AnalysisRun, world["run_id"])
            run.input_summary_json = {
                "structured_answer": json.loads(_report("ev-bridge")),
            }
            db.commit()

        # 模拟工具提交报告后没有额外 assistant/message 的 Harness 回合。
        m = _FakeManager([{"finalResponse": "", "finishReason": "completed"}])
        _run_harness(factory, world, m)

        assert _run_status(factory, world["run_id"]) == "completed"
        msgs = _assistant_messages(factory, world)
        assert len(msgs) == 1
        assert msgs[0].structured_answer_json["findings"][0]["evidence_ids"] == ["ev-bridge"]

    def test_repair_rescues_then_completed(self, factory, world):
        _add_evidence(factory, world, "ev-r1")
        m = _FakeManager([
            {"finalResponse": _report("ev-missing"), "finishReason": "stop"},
            {"finalResponse": _report("ev-r1"), "finishReason": "stop"},
        ])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) == "completed"
        assert len(m.calls) == 2  # 首回合 + 一次低温修复

    def test_repair_fails_then_degraded(self, factory, world):
        _add_evidence(factory, world, "ev-d1")
        m = _FakeManager([
            {"finalResponse": _report("ev-bad"), "finishReason": "stop"},
            {"finalResponse": _report("ev-bad"), "finishReason": "stop"},
        ])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) == "degraded"
        with factory() as db:
            run = db.get(AnalysisRun, world["run_id"])
            assert run.input_summary_json.get("validation_degraded") is True
            assert run.input_summary_json.get("repair_retried") is True
        msgs = _assistant_messages(factory, world)
        assert len(msgs) == 1
        assert "降级" in msgs[0].content_text

    def test_not_completed_before_validation(self, factory, world):
        """验证完成前不标记 completed：失败路径下 run 状态绝不是 completed。"""
        m = _FakeManager([{"finalResponse": "含 13800000000 电话的文本",
                           "finishReason": "stop"}])
        _run_harness(factory, world, m)
        assert _run_status(factory, world["run_id"]) != "completed"
