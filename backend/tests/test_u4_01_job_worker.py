"""U4-01 SQLite Worker 测试

测试要求：
- 默认单 Worker、FIFO
- 数据库事务认领任务，防止重复执行
- heartbeat、attempts、checkpoint、idempotency_key 生效
- 支持取消、重试、指数退避和最大次数
- 应用退出时停止接新任务，给当前任务有限收尾时间
- 启动时恢复 queued，审计 interrupted running
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.database import Base
from backend.app.models.agent_entities import BackgroundJob
from backend.app.services.job_worker import (
    JobWorker,
    compute_backoff_delay,
    submit_job,
    set_global_worker,
    get_global_worker,
    MAX_ATTEMPTS,
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
)


# ---------------------------------------------------------------------------
# 测试 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_factory():
    """创建临时文件 SQLite 数据库（内存数据库不支持跨线程共享）。"""
    import tempfile
    import os

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_worker_")
    os.close(fd)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()
    os.unlink(db_path)


@pytest.fixture
def worker(session_factory):
    """创建 Worker 实例（不自动启动）。"""
    w = JobWorker(
        session_factory,
        poll_interval=0.05,
        heartbeat_interval=0.5,
        shutdown_grace=2.0,
    )
    yield w
    w.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# submit_job 测试
# ---------------------------------------------------------------------------

class TestSubmitJob:
    def test_submit_creates_queued_job(self, session_factory):
        job_id = submit_job(session_factory, "echo", {"exam_id": 1})
        assert job_id > 0

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job is not None
            assert job.status == "queued"
            assert job.job_type == "echo"
            assert job.scope_json == {"exam_id": 1}
            assert job.attempts == 0

    def test_submit_with_idempotency_key(self, session_factory):
        key = "exam-1-stats-refresh"
        id1 = submit_job(session_factory, "stats_refresh", {"exam_id": 1},
                         idempotency_key=key)
        id2 = submit_job(session_factory, "stats_refresh", {"exam_id": 1},
                         idempotency_key=key)
        assert id1 == id2

    def test_submit_different_idempotency_keys(self, session_factory):
        id1 = submit_job(session_factory, "echo", idempotency_key="key-a")
        id2 = submit_job(session_factory, "echo", idempotency_key="key-b")
        assert id1 != id2

    def test_submit_idempotency_allows_new_after_complete(self, session_factory):
        key = "exam-1-echo"
        id1 = submit_job(session_factory, "echo", idempotency_key=key)
        with session_factory() as session:
            job = session.get(BackgroundJob, id1)
            job.status = "completed"
            session.commit()
        id2 = submit_job(session_factory, "echo", idempotency_key=key)
        assert id2 != id1


# ---------------------------------------------------------------------------
# FIFO 顺序测试
# ---------------------------------------------------------------------------

class TestFIFO:
    def test_fifo_order(self, session_factory, worker):
        results: list[int] = []

        def fifo_handler(job, session, checkpoint, is_cancelled):
            results.append(job.id)
            return checkpoint

        worker.register_handler("echo", fifo_handler)
        worker.start()

        id1 = submit_job(session_factory, "echo")
        id2 = submit_job(session_factory, "echo")
        id3 = submit_job(session_factory, "echo")

        time.sleep(0.5)
        worker.stop(timeout=2.0)

        assert results == [id1, id2, id3]


# ---------------------------------------------------------------------------
# 认领和执行测试
# ---------------------------------------------------------------------------

class TestClaimAndExecute:
    def test_claim_changes_status_to_running(self, session_factory, worker):
        executed = threading.Event()

        def handler(job, session, checkpoint, is_cancelled):
            assert job.status == "running"
            assert job.attempts == 1
            executed.set()
            return checkpoint

        worker.register_handler("echo", handler)
        worker.start()
        submit_job(session_factory, "echo")
        assert executed.wait(timeout=2.0)
        worker.stop(timeout=1.0)

    def test_checkpoint_persisted(self, session_factory, worker):
        def handler(job, session, checkpoint, is_cancelled):
            checkpoint["result"] = "done"
            checkpoint["step"] = 3
            return checkpoint

        worker.register_handler("echo", handler)
        worker.start()
        job_id = submit_job(session_factory, "echo")
        time.sleep(0.3)
        worker.stop(timeout=1.0)

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "completed"
            assert job.checkpoint_json.get("result") == "done"
            assert job.checkpoint_json.get("step") == 3

    def test_no_handler_fails_job(self, session_factory, worker):
        worker.start()
        job_id = submit_job(session_factory, "unknown_type")
        time.sleep(0.3)
        worker.stop(timeout=1.0)

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "failed"
            assert "无注册处理器" in (job.last_error or "")


# ---------------------------------------------------------------------------
# 取消测试
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancel_running_job(self, session_factory, worker):
        cancel_checked = threading.Event()

        def handler(job, session, checkpoint, is_cancelled):
            for _ in range(50):
                if is_cancelled():
                    cancel_checked.set()
                    return {"cancelled": True}
                time.sleep(0.02)
            return {"cancelled": False}

        worker.register_handler("echo", handler)
        worker.start()
        job_id = submit_job(session_factory, "echo")
        time.sleep(0.1)

        cancelled = worker.cancel_job(job_id)
        assert cancelled
        assert cancel_checked.wait(timeout=2.0)
        worker.stop(timeout=2.0)

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "cancelled"

    def test_cancel_queued_job_directly(self, session_factory, worker):
        # 先注册一个慢处理器让队列不空
        slow_started = threading.Event()

        def slow_handler(job, session, checkpoint, is_cancelled):
            slow_started.set()
            time.sleep(2.0)
            return checkpoint

        worker.register_handler("echo", slow_handler)
        worker.start()

        id1 = submit_job(session_factory, "echo")
        id2 = submit_job(session_factory, "echo")

        assert slow_started.wait(timeout=2.0)
        cancelled = worker.cancel_job(id2)
        assert cancelled

        with session_factory() as session:
            job = session.get(BackgroundJob, id2)
            assert job.status == "cancelled"

        worker.stop(timeout=3.0)


# ---------------------------------------------------------------------------
# 重试和指数退避测试
# ---------------------------------------------------------------------------

class TestRetry:
    def test_retry_on_failure(self, session_factory, worker):
        attempt_count = 0

        def failing_handler(job, session, checkpoint, is_cancelled):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 2:
                raise RuntimeError("模拟失败")
            return {"succeeded_on": attempt_count}

        worker.register_handler("echo", failing_handler)
        worker.start()
        job_id = submit_job(session_factory, "echo")

        # 等待重试完成（指数退避：第一次重试等 BACKOFF_BASE * 2^1 = 4s）
        time.sleep(6.0)
        worker.stop(timeout=2.0)

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "completed"
            assert attempt_count == 2

    def test_max_attempts_marks_failed(self, session_factory, worker):
        worker = JobWorker(
            session_factory,
            max_attempts=2,
            poll_interval=0.05,
            heartbeat_interval=0.5,
            shutdown_grace=2.0,
        )

        def always_fail(job, session, checkpoint, is_cancelled):
            raise RuntimeError("永远失败")

        worker.register_handler("echo", always_fail)
        worker.start()
        job_id = submit_job(session_factory, "echo")

        # 指数退避：第一次重试等 4s，第二次失败后标记 failed
        time.sleep(7.0)
        worker.stop(timeout=2.0)

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "failed"
            assert "最大重试次数" in (job.last_error or "")


# ---------------------------------------------------------------------------
# 指数退避测试
# ---------------------------------------------------------------------------

class TestBackoff:
    def test_backoff_increases_exponentially(self):
        d0 = compute_backoff_delay(0)
        d1 = compute_backoff_delay(1)
        d2 = compute_backoff_delay(2)
        d3 = compute_backoff_delay(3)
        assert d0 == BACKOFF_BASE_SECONDS
        assert d1 == BACKOFF_BASE_SECONDS * 2
        assert d2 == BACKOFF_BASE_SECONDS * 4
        assert d3 == BACKOFF_BASE_SECONDS * 8

    def test_backoff_capped_at_max(self):
        d_large = compute_backoff_delay(20)
        assert d_large == BACKOFF_MAX_SECONDS


# ---------------------------------------------------------------------------
# 启动恢复测试
# ---------------------------------------------------------------------------

class TestRecovery:
    def test_recover_queued_jobs_kept(self, session_factory, worker):
        """启动恢复：queued 任务保持入队。"""
        _create_job(session_factory, "echo", status="queued")
        _create_job(session_factory, "echo", status="queued")

        result = worker.recover_on_startup()
        assert result["recovered"] == 2
        assert result["interrupted"] == 0

        with session_factory() as session:
            from sqlalchemy import select
            jobs = session.scalars(
                select(BackgroundJob).where(BackgroundJob.status == "queued")
            ).all()
            assert len(jobs) == 2

    def test_recover_interrupted_running(self, session_factory, worker):
        """启动恢复：中断的 running（未达最大重试次数）重新入队。"""
        _create_job(session_factory, "echo", status="running", attempts=1)
        _create_job(session_factory, "echo", status="running", attempts=1)

        result = worker.recover_on_startup()
        assert result["recovered"] == 0
        assert result["requeued"] == 2
        assert result["interrupted"] == 0

        with session_factory() as session:
            from sqlalchemy import select
            jobs = session.scalars(
                select(BackgroundJob).where(BackgroundJob.status == "queued")
            ).all()
            assert len(jobs) == 2
            for job in jobs:
                assert job.attempts == 1  # 计数保留
                assert "重新入队" in (job.last_error or "")

    def test_recover_mixed_states(self, session_factory, worker):
        """启动恢复：混合状态正确处理。"""
        _create_job(session_factory, "echo", status="queued")
        _create_job(session_factory, "echo", status="running", attempts=1)
        _create_job(session_factory, "echo", status="running", attempts=3)
        _create_job(session_factory, "echo", status="completed")
        _create_job(session_factory, "echo", status="failed")

        result = worker.recover_on_startup()
        assert result["recovered"] == 1
        assert result["requeued"] == 1      # attempts=1 → 重新入队
        assert result["interrupted"] == 1   # attempts=3（达最大值）→ failed


# ---------------------------------------------------------------------------
# 优雅关闭测试
# ---------------------------------------------------------------------------

class TestGracefulShutdown:
    def test_stop_does_not_lose_running_job(self, session_factory, worker):
        """关闭时给当前任务收尾时间。"""
        completed = threading.Event()

        def handler(job, session, checkpoint, is_cancelled):
            time.sleep(0.3)
            checkpoint["completed"] = True
            completed.set()
            return checkpoint

        worker.register_handler("echo", handler)
        worker.start()
        job_id = submit_job(session_factory, "echo")

        time.sleep(0.1)  # 等任务开始
        worker.stop(timeout=2.0)  # 等任务完成

        assert completed.is_set()
        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "completed"

    def test_stop_ignores_new_jobs(self, session_factory, worker):
        """关闭后不再接受新任务。"""
        worker.start()
        worker.stop(timeout=0.5)

        # Worker 已停止，提交的任务不会被执行
        job_id = submit_job(session_factory, "echo")
        time.sleep(0.3)

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "queued"  # 仍在队列中


# ---------------------------------------------------------------------------
# 全局 Worker 测试
# ---------------------------------------------------------------------------

class TestGlobalWorker:
    def test_set_and_get_global_worker(self, worker):
        set_global_worker(worker)
        assert get_global_worker() is worker
        set_global_worker(None)
        assert get_global_worker() is None


# ---------------------------------------------------------------------------
# Heartbeat 测试
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_updates_timestamp(self, session_factory, worker):
        """heartbeat 定期更新 updated_at。"""
        original_updated = None

        def handler(job, session, checkpoint, is_cancelled):
            nonlocal original_updated
            original_updated = job.updated_at
            # 等待 heartbeat 更新
            time.sleep(1.0)
            return checkpoint

        worker = JobWorker(
            session_factory,
            poll_interval=0.05,
            heartbeat_interval=0.2,
            shutdown_grace=3.0,
        )
        worker.register_handler("echo", handler)
        worker.start()
        job_id = submit_job(session_factory, "echo")
        time.sleep(1.5)
        worker.stop(timeout=3.0)

        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job.status == "completed"
            # updated_at 应该比 original_updated 更新
            assert job.updated_at > original_updated


# ---------------------------------------------------------------------------
# 并发认领测试
# ---------------------------------------------------------------------------

class TestConcurrentClaim:
    def test_two_workers_dont_double_execute(self, session_factory):
        """两个 Worker 不会重复执行同一任务。"""
        execution_count = 0
        count_lock = threading.Lock()

        def handler(job, session, checkpoint, is_cancelled):
            nonlocal execution_count
            with count_lock:
                execution_count += 1
            time.sleep(0.2)
            return checkpoint

        w1 = JobWorker(session_factory, poll_interval=0.05)
        w2 = JobWorker(session_factory, poll_interval=0.05)
        w1.register_handler("echo", handler)
        w2.register_handler("echo", handler)

        job_id = submit_job(session_factory, "echo")

        w1.start()
        w2.start()
        time.sleep(1.0)
        w1.stop(timeout=2.0)
        w2.stop(timeout=2.0)

        assert execution_count == 1  # 只执行一次


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _create_job(
    session_factory,
    job_type: str = "echo",
    scope: dict | None = None,
    status: str = "queued",
    idempotency_key: str | None = None,
    attempts: int = 0,
) -> BackgroundJob:
    """直接在数据库中创建任务。"""
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        job = BackgroundJob(
            job_type=job_type,
            scope_json=scope or {},
            status=status,
            progress=0.0,
            checkpoint_json={},
            idempotency_key=idempotency_key,
            attempts=attempts,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        session.expunge(job)
        return job

# ---------------------------------------------------------------------------
# 评审修复回归：幂等补 waiting_ocr + 心跳超时僵尸恢复（P1-3 / P1-4）
# ---------------------------------------------------------------------------

def test_submit_job_idempotent_while_waiting_ocr(session_factory):
    """评审 P1-3：waiting_ocr 是 Worker 自己产生的状态，
    重复提交图片附件不得创建第二个任务（重复调模型、重复计费）。"""
    job_id = submit_job(session_factory, "attachment_parse", idempotency_key="att-1")
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        job.status = "waiting_ocr"
        session.commit()
    again = submit_job(session_factory, "attachment_parse", idempotency_key="att-1")
    assert again == job_id
    with session_factory() as session:
        count = len(session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key == "att-1")
        ).all())
    assert count == 1


def test_explicit_retry_requeues_waiting_ocr_without_duplicate(session_factory):
    job_id = submit_job(session_factory, "attachment_parse", idempotency_key="att-ocr")
    with session_factory() as session:
        session.get(BackgroundJob, job_id).status = "waiting_ocr"
        session.commit()
    retried = submit_job(session_factory, "attachment_parse",
                         idempotency_key="att-ocr", retry_waiting_ocr=True)
    assert retried == job_id
    with session_factory() as session:
        assert session.get(BackgroundJob, job_id).status == "queued"


def test_zombie_running_job_recovered_after_heartbeat_timeout(session_factory, worker):
    """评审 P1-4：heartbeat 超时的僵尸 running 任务被看门狗按重试语义恢复。"""
    from sqlalchemy import select as _select
    job_id = submit_job(session_factory, "attachment_parse", idempotency_key="z-1")
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        job.status = "running"
        job.updated_at = stale_time  # 心跳停更 120 秒（> 60 秒阈值）
        session.commit()

    recovered = worker._recover_zombie_jobs()
    assert recovered == 1
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job.status == "queued"
        assert job.next_attempt_at is not None
        assert "心跳超时" in job.last_error


def test_zombie_job_at_max_attempts_marked_failed(session_factory, worker):
    job_id = submit_job(session_factory, "attachment_parse", idempotency_key="z-2")
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        job.status = "running"
        job.attempts = MAX_ATTEMPTS
        job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        session.commit()

    assert worker._recover_zombie_jobs() == 1
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job.status == "failed"
        assert job.completed_at is not None


def test_zombie_recovery_skips_current_running_job(session_factory, worker):
    """当前正在执行的任务（心跳正常刷新）不得被看门狗误杀。"""
    from sqlalchemy import select as _select
    job_id = submit_job(session_factory, "attachment_parse", idempotency_key="z-3")
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        job.status = "running"
        job.updated_at = stale_time
        session.commit()
    worker._current_job_id = job_id  # 模拟该任务正在当前线程执行

    assert worker._recover_zombie_jobs() == 0
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job.status == "running"
    worker._current_job_id = None
