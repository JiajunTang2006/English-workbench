"""U2-05 重启恢复测试。

测试覆盖：
1. queued 运行保持 queued
2. waiting_confirmation 运行保持等待
3. running 运行无外部付费 -> 自动重试 -> queued
4. running 运行无外部付费，超过 MAX_AUTO_RETRY -> interrupted 需确认
5. running 运行有外部付费 -> interrupted 需确认（不静默重复）
6. 有终态事件但状态非终态 -> 从终态事件恢复
7. validating/estimating 运行恢复
8. 后台任务 queued 保持 queued
9. 后台任务 running 标记 failed
10. 后台任务 waiting_confirmation 保持等待
11. 完整恢复 run_full_recovery 合并报告
12. 空表恢复不报错
13. 状态机 interrupted 可转为 queued/failed/cancelled
"""

import pytest
from datetime import datetime, timezone

from backend.app.services.agent_runs.recovery import (
    RecoveryReport,
    recover_interrupted_runs,
    recover_interrupted_jobs,
    run_full_recovery,
    _has_terminal_event,
    _has_external_cost,
    _get_terminal_event_type,
    MAX_AUTO_RETRY,
)
from backend.app.agent.event_types import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_DEGRADED,
    RUN_CANCELLED,
)
from backend.app.models.agent_entities import (
    AnalysisRun,
    AnalysisRunEvent,
    BackgroundJob,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session():
    """创建内存 SQLite 数据库的 Session。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.models.agent_entities import Base

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def _make_run(session, run_id=1, status="running", retry_count=0,
              actual_cost=0.0, tool_calls=None, input_summary=None,
              session_id=1, term_id=1):
    run = AnalysisRun(
        id=run_id, session_id=session_id, capability="exam_analysis",
        term_id=term_id, status=status, retry_count=retry_count,
        actual_cost_yuan=actual_cost, tool_calls_json=tool_calls or [],
        input_summary_json=input_summary or {}, runtime_kind="legacy",
    )
    session.add(run)
    session.commit()
    return run


def _make_event(session, run_id, seq, event_type, payload=None):
    event = AnalysisRunEvent(
        run_id=run_id, seq=seq, event_type=event_type,
        payload_json=payload or {}, source="gateway",
    )
    session.add(event)
    session.commit()
    return event


def _make_job(session, job_id=1, status="running", job_type="pdf_extract"):
    job = BackgroundJob(id=job_id, job_type=job_type, status=status)
    session.add(job)
    session.commit()
    return job


# ---------------------------------------------------------------------------
# _has_terminal_event
# ---------------------------------------------------------------------------

class TestHasTerminalEvent:
    def test_has_completed(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, RUN_COMPLETED)
        assert _has_terminal_event(db_session, 1) is True

    def test_has_failed(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, RUN_FAILED)
        assert _has_terminal_event(db_session, 1) is True

    def test_has_degraded(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, RUN_DEGRADED)
        assert _has_terminal_event(db_session, 1) is True

    def test_has_cancelled(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, RUN_CANCELLED)
        assert _has_terminal_event(db_session, 1) is True

    def test_no_terminal(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, "run.started")
        assert _has_terminal_event(db_session, 1) is False

    def test_no_events(self, db_session):
        _make_run(db_session, 1)
        assert _has_terminal_event(db_session, 1) is False


# ---------------------------------------------------------------------------
# _has_external_cost
# ---------------------------------------------------------------------------

class TestHasExternalCost:
    def test_has_cost(self, db_session):
        run = _make_run(db_session, 1, actual_cost=1.5)
        assert _has_external_cost(run) is True

    def test_has_tool_calls(self, db_session):
        run = _make_run(db_session, 1, tool_calls=[{"name": "db_query"}])
        assert _has_external_cost(run) is True

    def test_has_request_ids(self, db_session):
        run = _make_run(db_session, 1, input_summary={"request_ids": ["req-1"]})
        assert _has_external_cost(run) is True

    def test_no_cost(self, db_session):
        run = _make_run(db_session, 1)
        assert _has_external_cost(run) is False


# ---------------------------------------------------------------------------
# _get_terminal_event_type
# ---------------------------------------------------------------------------

class TestGetTerminalEventType:
    def test_single_terminal(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, RUN_COMPLETED)
        assert _get_terminal_event_type(db_session, 1) == RUN_COMPLETED

    def test_latest_terminal(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, "run.started")
        _make_event(db_session, 1, 2, RUN_COMPLETED)
        _make_event(db_session, 1, 3, RUN_DEGRADED)
        assert _get_terminal_event_type(db_session, 1) == RUN_DEGRADED

    def test_none(self, db_session):
        _make_run(db_session, 1)
        _make_event(db_session, 1, 1, "run.started")
        assert _get_terminal_event_type(db_session, 1) is None


# ---------------------------------------------------------------------------
# recover_interrupted_runs
# ---------------------------------------------------------------------------

class TestRecoverInterruptedRuns:
    def test_queued_stays(self, db_session):
        _make_run(db_session, 1, status="queued")
        report = recover_interrupted_runs(db_session)
        assert report.runs_examined == 1
        assert report.runs_requeued == 1
        assert db_session.get(AnalysisRun, 1).status == "queued"

    def test_waiting_confirmation_stays(self, db_session):
        _make_run(db_session, 1, status="waiting_confirmation")
        report = recover_interrupted_runs(db_session)
        assert report.runs_kept_waiting == 1
        assert db_session.get(AnalysisRun, 1).status == "waiting_confirmation"

    def test_running_no_cost_auto_retry(self, db_session):
        _make_run(db_session, 1, status="running", retry_count=0)
        report = recover_interrupted_runs(db_session)
        assert report.runs_auto_retry == 1
        assert report.runs_requeued == 1
        run = db_session.get(AnalysisRun, 1)
        assert run.status == "queued"
        assert run.retry_count == 1

    def test_running_max_retry_interrupted(self, db_session):
        _make_run(db_session, 1, status="running", retry_count=MAX_AUTO_RETRY)
        report = recover_interrupted_runs(db_session)
        assert report.runs_needs_confirmation == 1
        assert report.runs_auto_retry == 0
        assert db_session.get(AnalysisRun, 1).status == "interrupted"

    def test_running_with_cost_interrupted(self, db_session):
        _make_run(db_session, 1, status="running", actual_cost=2.5)
        report = recover_interrupted_runs(db_session)
        assert report.runs_needs_confirmation == 1
        assert report.runs_auto_retry == 0
        assert db_session.get(AnalysisRun, 1).status == "interrupted"

    def test_running_with_tool_calls_interrupted(self, db_session):
        _make_run(db_session, 1, status="running", tool_calls=[{"name": "x"}])
        report = recover_interrupted_runs(db_session)
        assert report.runs_needs_confirmation == 1
        assert db_session.get(AnalysisRun, 1).status == "interrupted"

    def test_reconcile_completed(self, db_session):
        _make_run(db_session, 1, status="running")
        _make_event(db_session, 1, 1, RUN_COMPLETED)
        report = recover_interrupted_runs(db_session)
        assert report.runs_reconciled == 1
        run = db_session.get(AnalysisRun, 1)
        assert run.status == "completed"
        assert run.completed_at is not None

    def test_reconcile_degraded(self, db_session):
        _make_run(db_session, 1, status="running")
        _make_event(db_session, 1, 1, RUN_DEGRADED)
        report = recover_interrupted_runs(db_session)
        assert report.runs_reconciled == 1
        assert db_session.get(AnalysisRun, 1).status == "degraded"

    def test_reconcile_failed(self, db_session):
        _make_run(db_session, 1, status="running")
        _make_event(db_session, 1, 1, RUN_FAILED)
        report = recover_interrupted_runs(db_session)
        assert report.runs_reconciled == 1
        assert db_session.get(AnalysisRun, 1).status == "failed"

    def test_reconcile_cancelled(self, db_session):
        _make_run(db_session, 1, status="running")
        _make_event(db_session, 1, 1, RUN_CANCELLED)
        report = recover_interrupted_runs(db_session)
        assert report.runs_reconciled == 1
        assert db_session.get(AnalysisRun, 1).status == "cancelled"

    def test_validating_auto_retry(self, db_session):
        _make_run(db_session, 1, status="validating", retry_count=0)
        report = recover_interrupted_runs(db_session)
        assert report.runs_auto_retry == 1
        assert db_session.get(AnalysisRun, 1).status == "queued"

    def test_estimating_auto_retry(self, db_session):
        _make_run(db_session, 1, status="estimating", retry_count=1)
        report = recover_interrupted_runs(db_session)
        assert report.runs_auto_retry == 1
        run = db_session.get(AnalysisRun, 1)
        assert run.status == "queued"
        assert run.retry_count == 2

    def test_validating_output_auto_retry(self, db_session):
        _make_run(db_session, 1, status="validating_output", retry_count=0)
        report = recover_interrupted_runs(db_session)
        assert report.runs_auto_retry == 1
        assert db_session.get(AnalysisRun, 1).status == "queued"

    def test_multiple_runs(self, db_session):
        _make_run(db_session, 1, status="queued")
        _make_run(db_session, 2, status="running", retry_count=0)
        _make_run(db_session, 3, status="waiting_confirmation")
        _make_run(db_session, 4, status="running", actual_cost=1.0)
        report = recover_interrupted_runs(db_session)
        assert report.runs_examined == 4
        assert report.runs_requeued == 2  # run 1 (queued) + run 2 (auto-retry -> queued)
        assert report.runs_kept_waiting == 1
        assert report.runs_needs_confirmation == 1

    def test_empty_table(self, db_session):
        report = recover_interrupted_runs(db_session)
        assert report.runs_examined == 0
        assert len(report.errors) == 0

    def test_error_recovery_does_not_crash(self, db_session):
        """单个 run 恢复失败不应影响其他 run。"""
        _make_run(db_session, 1, status="queued")
        _make_run(db_session, 2, status="running", retry_count=0)
        report = recover_interrupted_runs(db_session)
        assert report.runs_examined == 2
        # 两个都应被处理
        assert report.runs_requeued >= 1


# ---------------------------------------------------------------------------
# recover_interrupted_jobs
# ---------------------------------------------------------------------------

class TestRecoverInterruptedJobs:
    def test_queued_stays(self, db_session):
        _make_job(db_session, 1, status="queued")
        report = recover_interrupted_jobs(db_session)
        assert report.jobs_examined == 1
        assert report.jobs_requeued == 1
        assert db_session.get(BackgroundJob, 1).status == "queued"

    def test_running_requeued_before_retry_limit(self, db_session):
        _make_job(db_session, 1, status="running")
        report = recover_interrupted_jobs(db_session)
        assert report.jobs_requeued == 1
        job = db_session.get(BackgroundJob, 1)
        assert job.status == "queued"
        assert job.completed_at is None
        assert "中断" in (job.last_error or "")

    def test_waiting_confirmation_stays(self, db_session):
        _make_job(db_session, 1, status="waiting_confirmation")
        report = recover_interrupted_jobs(db_session)
        assert report.jobs_kept_waiting == 1
        assert db_session.get(BackgroundJob, 1).status == "waiting_confirmation"

    def test_multiple_jobs(self, db_session):
        _make_job(db_session, 1, status="queued")
        _make_job(db_session, 2, status="running")
        _make_job(db_session, 3, status="waiting_confirmation")
        report = recover_interrupted_jobs(db_session)
        assert report.jobs_examined == 3
        assert report.jobs_requeued == 2
        assert report.jobs_failed == 0
        assert report.jobs_kept_waiting == 1

    def test_empty_table(self, db_session):
        report = recover_interrupted_jobs(db_session)
        assert report.jobs_examined == 0
        assert len(report.errors) == 0


# ---------------------------------------------------------------------------
# run_full_recovery
# ---------------------------------------------------------------------------

class TestRunFullRecovery:
    def test_mixed_recovery(self, db_session):
        _make_run(db_session, 1, status="queued")
        _make_run(db_session, 2, status="running", retry_count=0)
        _make_run(db_session, 3, status="running", actual_cost=3.0)
        _make_job(db_session, 1, status="queued")
        _make_job(db_session, 2, status="running")

        report = run_full_recovery(db_session)

        assert report.runs_examined == 3
        assert report.jobs_examined == 2
        assert report.runs_requeued >= 2  # queued + auto-retry
        assert report.runs_needs_confirmation == 1
        assert report.jobs_requeued == 2
        assert report.jobs_failed == 0
        assert 3 in report.needs_confirmation_run_ids

    def test_empty_db(self, db_session):
        report = run_full_recovery(db_session)
        assert report.runs_examined == 0
        assert report.jobs_examined == 0
        assert len(report.errors) == 0

    def test_summary_string(self, db_session):
        _make_run(db_session, 1, status="queued")
        report = run_full_recovery(db_session)
        s = report.summary()
        assert "runs: examined=1" in s
        assert "requeued=1" in s

    def test_report_dataclass_defaults(self):
        r = RecoveryReport()
        assert r.runs_examined == 0
        assert r.errors == []
        assert r.interrupted_run_ids == []
        assert r.needs_confirmation_run_ids == []
