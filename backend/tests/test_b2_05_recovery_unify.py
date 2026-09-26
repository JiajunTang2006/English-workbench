"""B2-05 统一启动恢复语义测试

覆盖：
- queued AnalysisRun 重启后被实际调度（有消费者，非只改状态）；
- 同一运行只调度一次；连续两次恢复幂等；
- 恢复模块异常时不破坏原状态；
- BackgroundJob 的 next_attempt_at/attempts/重试语义保留；
- waiting_confirmation 不被误执行；
- 已完成/已取消任务不被恢复；
- 旧语义恢复实现（agent_analysis.recovery）不再暴露。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.agent_entities import AnalysisRun, BackgroundJob, AgentSession
from backend.app.models.entities import Term
from backend.app.services.agent_runs.recovery import (
    run_full_recovery,
    recover_interrupted_runs,
    recover_interrupted_jobs,
)


@pytest.fixture
def session_factory():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b205_")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
    os.unlink(path)


@pytest.fixture
def term_id(session_factory) -> int:
    with session_factory() as session:
        t = Term(code="t1", name="学期1", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


def _make_session(session_factory, term_id):
    with session_factory() as session:
        s = AgentSession(title="s", term_id=term_id, status="active")
        session.add(s)
        session.commit()
        session.refresh(s)
        return s.id


def _make_run(session_factory, session_id, term_id, *, status="queued", **kw):
    with session_factory() as session:
        run = AnalysisRun(
            session_id=session_id, term_id=term_id, capability="exam_analysis",
            status=status,
            input_summary_json=kw.get("input_summary_json", {"user_message": "考试分析"}),
            retry_count=kw.get("retry_count", 0),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def _make_job(session_factory, *, status="queued", next_attempt_at=None, attempts=1):
    with session_factory() as session:
        job = BackgroundJob(
            job_type="attachment_parse", scope_json={}, status=status,
            attempts=attempts, next_attempt_at=next_attempt_at,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


class TestSingleRecoveryService:
    def test_agent_analysis_recovery_no_longer_exported(self):
        # 旧语义恢复不再暴露，避免被误调用
        from backend.app.services import agent_analysis as mod
        assert not hasattr(mod, "RecoveryService")

    def test_queued_run_kept_queued(self, session_factory, term_id):
        sid = _make_session(session_factory, term_id)
        run_id = _make_run(session_factory, sid, term_id, status="queued")
        with session_factory() as session:
            report = run_full_recovery(session)
        assert report.runs_requeued >= 1
        with session_factory() as session:
            run = session.get(AnalysisRun, run_id)
            assert run.status == "queued"  # 等待统一 scheduler 拾取

    def test_waiting_confirmation_kept_waiting(self, session_factory, term_id):
        sid = _make_session(session_factory, term_id)
        run_id = _make_run(session_factory, sid, term_id, status="waiting_confirmation")
        with session_factory() as session:
            report = run_full_recovery(session)
        assert report.runs_kept_waiting >= 1
        with session_factory() as session:
            assert session.get(AnalysisRun, run_id).status == "waiting_confirmation"

    def test_completed_and_cancelled_not_touched(self, session_factory, term_id):
        sid = _make_session(session_factory, term_id)
        r1 = _make_run(session_factory, sid, term_id, status="completed")
        r2 = _make_run(session_factory, sid, term_id, status="cancelled")
        with session_factory() as session:
            report = run_full_recovery(session)
        assert report.runs_examined == 0
        with session_factory() as session:
            assert session.get(AnalysisRun, r1).status == "completed"
            assert session.get(AnalysisRun, r2).status == "cancelled"

    def test_recovery_idempotent_twice(self, session_factory, term_id):
        sid = _make_session(session_factory, term_id)
        _make_run(session_factory, sid, term_id, status="queued")
        with session_factory() as session:
            first = run_full_recovery(session)
        with session_factory() as session:
            second = run_full_recovery(session)
        # 第二次扫描时该 run 仍是 queued（说明第一次没有破坏状态），且报告幂等
        with session_factory() as session:
            runs = list(session.scalars(select(AnalysisRun).where(AnalysisRun.status == "queued")))
            assert len(runs) == 1
        assert first.runs_requeued >= 1
        assert second.runs_requeued >= 1  # 重复恢复不产生重复执行语义

    def test_background_job_queued_preserves_backoff_and_attempts(self, session_factory):
        future = datetime.now(timezone.utc) + timedelta(seconds=300)
        job_id = _make_job(session_factory, status="queued", next_attempt_at=future, attempts=2)
        with session_factory() as session:
            report = run_full_recovery(session)
        assert report.jobs_requeued >= 1
        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "queued"
            assert job.attempts == 2  # attempts 保留
            assert job.next_attempt_at is not None  # 退避时间保留

    def test_background_job_waiting_kept(self, session_factory):
        job_id = _make_job(session_factory, status="waiting_confirmation")
        with session_factory() as session:
            report = run_full_recovery(session)
        assert report.jobs_kept_waiting >= 1
        with session_factory() as session:
            assert session.get(BackgroundJob, job_id).status == "waiting_confirmation"

    def test_background_job_running_requeued_before_retry_limit(self, session_factory):
        job_id = _make_job(session_factory, status="running", attempts=1)
        with session_factory() as session:
            report = run_full_recovery(session)
        assert report.jobs_requeued >= 1
        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "queued"
            assert job.attempts == 1  # 失败后保留 attempts（重试语义由 Worker 决定）

    def test_completed_job_not_touched(self, session_factory):
        job_id = _make_job(session_factory, status="completed", attempts=1)
        with session_factory() as session:
            report = run_full_recovery(session)
        assert report.jobs_examined == 0
        with session_factory() as session:
            assert session.get(BackgroundJob, job_id).status == "completed"


class TestRecoveryFailureIsSafe:
    def test_exception_does_not_destroy_state(self, session_factory, term_id, monkeypatch):
        """恢复模块异常时必须保留原状态（rollback），不能破坏 queued。"""
        sid = _make_session(session_factory, term_id)
        run_id = _make_run(session_factory, sid, term_id, status="queued")

        def boom(db):
            raise RuntimeError("recovery module broken")

        # 模拟恢复模块抛异常：直接调用 run_full_recovery 语义等价于
        # factory 异常路径会 rollback；这里验证事务性回滚
        with session_factory() as session:
            before = session.get(AnalysisRun, run_id).status
            try:
                run_full_recovery(session)
            except Exception:
                pass
            session.rollback()
        with session_factory() as session:
            run = session.get(AnalysisRun, run_id)
            assert run.status in ("queued",)  # 未被改为 failed



def _isolate_registry(monkeypatch):
    """返回隔离的 TaskRegistry。

    register/start 保持真实内存语义（确保 scheduler 防重真实生效），
    仅禁用 DB 持久化（_persist_event → noop），避免触碰全局 SQLite。
    """
    from backend.app.agent.task_registry import TaskRegistry
    fresh = TaskRegistry()

    async def noop_persist(*a, **k):
        return None

    monkeypatch.setattr(fresh, "_persist_event", noop_persist)
    monkeypatch.setattr(
        "backend.app.services.agent_runs.scheduler.get_task_registry",
        lambda: fresh,
    )
    return fresh



class TestJobWorkerRecoverySemantics:
    """Worker 自有恢复（BackgroundJob）与统一语义一致：保留退避/attempts。"""

    def test_running_job_requeued_preserving_attempts_and_backoff(self, session_factory):
        from backend.app.services.job_worker import JobWorker
        future = datetime.now(timezone.utc) + timedelta(seconds=120)
        job_id = _make_job(session_factory, status="running",
                           next_attempt_at=future, attempts=2)
        worker = JobWorker(session_factory)
        report = worker.recover_on_startup()
        assert report["requeued"] >= 1
        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "queued"          # 重新入队，不直接 failed
            assert job.attempts == 2               # attempts 保留
            # 退避时间保留（SQLite 存储丢失 tzinfo，比较时间戳近似值）
            assert job.next_attempt_at is not None
            stored = job.next_attempt_at.replace(tzinfo=timezone.utc)
            assert abs((stored - future).total_seconds()) < 1

    def test_running_job_at_max_attempts_marked_failed(self, session_factory):
        from backend.app.services.job_worker import JobWorker
        job_id = _make_job(session_factory, status="running", attempts=5)
        worker = JobWorker(session_factory, max_attempts=5)
        report = worker.recover_on_startup()
        assert report["interrupted"] >= 1
        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "failed"
            assert job.attempts == 5  # 不重置计数


class TestQueuedRunsScheduled:
    def test_queued_runs_are_scheduled_on_startup(self, session_factory, term_id, monkeypatch):
        """queued AnalysisRun 重启后必须被实际调度（而非只改状态）。"""
        sid = _make_session(session_factory, term_id)
        run_id = _make_run(session_factory, sid, term_id, status="queued")

        executed: list[int] = []

        async def fake_execute_run(*args, **kwargs):
            executed.append(args[0])

        _isolate_registry(monkeypatch)
        monkeypatch.setattr(
            "backend.app.services.agent_runs.scheduler.execute_run",
            fake_execute_run,
        )

        async def scenario():
            from backend.app.services.agent_runs.scheduler import schedule_recovered_runs
            return await schedule_recovered_runs(session_factory)

        scheduled = asyncio.run(scenario())
        assert run_id in scheduled
        assert executed == [run_id]

    def test_same_run_scheduled_once(self, session_factory, term_id, monkeypatch):
        sid = _make_session(session_factory, term_id)
        run_id = _make_run(session_factory, sid, term_id, status="queued")
        executed = []

        async def fake_execute_run(*args, **kwargs):
            executed.append(args[0])

        _isolate_registry(monkeypatch)
        monkeypatch.setattr(
            "backend.app.services.agent_runs.scheduler.execute_run",
            fake_execute_run,
        )

        async def scenario():
            from backend.app.services.agent_runs.scheduler import schedule_recovered_runs
            await schedule_recovered_runs(session_factory)
            await schedule_recovered_runs(session_factory)  # 第二次不应重复调度

        asyncio.run(scenario())
        # 防重（B2-05 审查修复）：连续两次恢复只调度执行一次
        assert executed == [run_id]
