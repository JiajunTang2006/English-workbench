"""B3-09 恢复防重（补测）：未知付费请求不自动重试

验证：已产生外部模型请求但结果未知的运行（running + 有 harness/provider 痕迹），
启动恢复时标记 interrupted 等待教师确认，绝不自动重新调度付费调用；
waiting_confirmation 保持等待不被自动执行。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.entities import Term
from backend.app.models.agent_entities import AgentSession, AnalysisRun
from backend.app.services.agent_runs.recovery import run_full_recovery


@pytest.fixture
def session_factory():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b309_")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
    os.unlink(path)


@pytest.fixture
def term_id(session_factory) -> int:
    with session_factory() as db:
        t = Term(code="t1", name="2026春", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        db.add(t)
        db.commit()
        return t.id


def _make_run(session_factory, term_id, *, status, harness=False, retry=0):
    with session_factory() as db:
        run = AnalysisRun(session_id=1, term_id=term_id,
                          capability="exam_analysis", status=status,
                          retry_count=retry,
                          harness_session_id="tm-1-xyz" if harness else None,
                          input_summary_json={
                              "user_message": "x",
                              # 已产生外部付费请求（provider request ids）
                              "request_ids": ["req-paid-1"] if harness else [],
                          })
        db.add(run)
        db.commit()
        return run.id


class TestRecoveryNoAutoRepay:

    def test_unknown_paid_request_interrupted_not_requeued(self, session_factory, term_id):
        """running + 已发外部请求（harness 痕迹）→ interrupted，不自动重试付费。"""
        rid = _make_run(session_factory, term_id, status="running", harness=True)
        with session_factory() as db:
            report = run_full_recovery(db)
        with session_factory() as db:
            run = db.get(AnalysisRun, rid)
            assert run.status == "interrupted"
            assert "需教师确认" in (run.error_message or "")
        assert rid in report.needs_confirmation_run_ids

    def test_waiting_confirmation_not_executed(self, session_factory, term_id):
        rid = _make_run(session_factory, term_id, status="waiting_confirmation")
        with session_factory() as db:
            report = run_full_recovery(db)
        with session_factory() as db:
            run = db.get(AnalysisRun, rid)
            assert run.status == "waiting_confirmation"  # 保持等待，不自动执行

    def test_completed_not_recovered(self, session_factory, term_id):
        rid = _make_run(session_factory, term_id, status="completed")
        with session_factory() as db:
            run_full_recovery(db)
        with session_factory() as db:
            run = db.get(AnalysisRun, rid)
            assert run.status == "completed"

    def test_queued_requeued_once(self, session_factory, term_id):
        """连续两次恢复不改变 run 状态（幂等），实际只调度一次的语义由
        scheduler 防重 + B2-05 executed==[run_id] 保证。"""
        rid = _make_run(session_factory, term_id, status="queued")
        with session_factory() as db:
            report1 = run_full_recovery(db)
            report2 = run_full_recovery(db)
            run = db.get(AnalysisRun, rid)
            assert run.status in ("queued", "running")
        assert report2.runs_requeued == report1.runs_requeued
        assert not report2.errors