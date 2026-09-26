"""S0-05 附件解析失败状态语义测试

覆盖目标：
  成功解析：  附件 pending_review，任务 completed
  图片 OCR：  附件 pending_ocr，任务 waiting_ocr（不伪装完全成功）
  损坏文件：  附件 parse_failed，任务 failed
  不支持格式：附件 parse_failed，任务 failed 且不可重试
  临时错误：  按指数退避重试
  超过最大重试：最终 failed
  用户取消：  不会被覆盖为 completed
  正常 PDF：  completed + pending_review
  防御性：   handler 返回 parse_failed checkpoint 时任务不得 completed
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.database import Base
from backend.app.models.agent_entities import BackgroundJob
from backend.app.models.entities import Attachment, Term
from backend.app.services.job_worker import (
    JobWorker,
    PermanentJobError,
    RetryableJobError,
    submit_job,
)
from backend.app.services.job_handlers.base import register_all_handlers


MINIMAL_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 55 >> stream
BT /F1 24 Tf 72 720 Td (Hello PDF) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R /Size 6 >>
%%EOF
"""

BROKEN_PDF = b"%PDF-1.4\n%%EOF broken trailer missing objects"


@pytest.fixture
def session_factory():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_s005_")
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
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "attachments").mkdir()
    return d


@pytest.fixture
def term(session_factory):
    with session_factory() as session:
        t = Term(
            code="2025-fall",
            name="2025秋季",
            starts_on=date(2025, 9, 1),
            ends_on=date(2026, 1, 31),
            status="active",
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


@pytest.fixture
def worker(session_factory, monkeypatch):
    # 缩短指数退避基数，避免测试等待真实 2s/4s/8s
    monkeypatch.setattr("backend.app.services.job_worker.BACKOFF_BASE_SECONDS", 0.02)
    monkeypatch.setattr("backend.app.services.job_worker.BACKOFF_MAX_SECONDS", 0.2)
    w = JobWorker(
        session_factory,
        poll_interval=0.02,
        heartbeat_interval=0.2,
        shutdown_grace=2.0,
    )
    register_all_handlers(w)
    w.start()
    yield w
    w.stop(timeout=2.0)


def _create_attachment(
    session_factory,
    term_id: int,
    *,
    content: bytes,
    original_name: str = "test.txt",
    mime_type: str = "text/plain",
    data_dir: Path = None,
    force_valid: bool = True,
) -> int:
    """创建附件；expected=False 时大小/哈希不匹配（模拟损坏）。"""
    digest = hashlib.sha256(content).hexdigest()
    suffix = Path(original_name).suffix.lower()
    storage_name = f"term_{term_id}_{hashlib.sha256(original_name.encode()).hexdigest()[:16]}{suffix}"
    if data_dir is not None:
        stored = content if force_valid else b"tampered bytes not matching the declared hash"
        (data_dir / "attachments" / storage_name).write_bytes(stored)
    with session_factory() as session:
        att = Attachment(
            term_id=term_id,
            title=original_name,
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=digest,
            storage_name=storage_name,
            metadata_json={},
        )
        session.add(att)
        session.commit()
        session.refresh(att)
        return att.id


def _submit_parse(session_factory, att_id: int, data_dir: Path) -> int:
    return submit_job(
        session_factory,
        "attachment_parse",
        {
            "attachment_id": att_id,
            "purpose": "exam_paper",
            "settings_data_dir": str(data_dir),
        },
        idempotency_key=f"parse_{att_id}",
    )


def _wait_for_status(session_factory, job_id: int, statuses: set[str], timeout: float = 15.0) -> BackgroundJob:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job and job.status in statuses:
                return job
        time.sleep(0.02)
    with session_factory() as session:
        job = session.get(BackgroundJob, job_id)
        raise AssertionError(f"job #{job_id} 未在 {timeout}s 内进入 {statuses}，当前 status={job.status if job else 'missing'}, last_error={job.last_error if job else ''}")


def _attachment_parsed(session_factory, att_id: int) -> dict[str, Any]:
    with session_factory() as session:
        att = session.get(Attachment, att_id)
        return (att.metadata_json or {}).get("parsed", {})


# ---------------------------------------------------------------------------
# 场景测试
# ---------------------------------------------------------------------------


class TestParseFailureStates:
    def test_normal_pdf_completed_and_pending_review(self, session_factory, data_dir, term, worker):
        att_id = _create_attachment(
            session_factory, term, content=MINIMAL_PDF,
            original_name="paper.pdf", mime_type="application/pdf", data_dir=data_dir,
        )
        job_id = _submit_parse(session_factory, att_id, data_dir)
        job = _wait_for_status(session_factory, job_id, {"completed"})
        assert job.status == "completed"
        assert job.completed_at is not None
        parsed = _attachment_parsed(session_factory, att_id)
        assert parsed["status"] == "pending_review"
        assert parsed["error"] is None
        assert "Hello PDF" in parsed["content"]

    def test_corrupted_pdf_attachment_failed_and_task_failed(self, session_factory, data_dir, term, worker):
        att_id = _create_attachment(
            session_factory, term, content=BROKEN_PDF,
            original_name="broken.pdf", mime_type="application/pdf", data_dir=data_dir,
        )
        job_id = _submit_parse(session_factory, att_id, data_dir)
        job = _wait_for_status(session_factory, job_id, {"failed"})
        assert job.status == "failed"
        assert job.completed_at is not None
        parsed = _attachment_parsed(session_factory, att_id)
        assert parsed["status"] == "parse_failed"
        assert parsed["error"]
        assert job.last_error  # 错误信息同时保存到附件 metadata 和后台任务

    def test_storage_mismatch_fails_immediately(self, session_factory, data_dir, term, worker):
        """文件损坏（哈希/大小不匹配）→ 永久失败，不重试。"""
        att_id = _create_attachment(
            session_factory, term, content=b"real content",
            original_name="broken.txt", data_dir=data_dir, force_valid=False,
        )
        job_id = _submit_parse(session_factory, att_id, data_dir)
        job = _wait_for_status(session_factory, job_id, {"failed"})
        assert job.status == "failed"
        assert job.attempts == 1  # 不可重试
        parsed = _attachment_parsed(session_factory, att_id)
        assert parsed["status"] == "parse_failed"
        assert "损坏" in (job.last_error or "")

    def test_unsupported_format_fails_without_retry(self, session_factory, data_dir, term, worker):
        att_id = _create_attachment(
            session_factory, term, content=b"binary data",
            original_name="file.xyz", mime_type="application/octet-stream", data_dir=data_dir,
        )
        job_id = _submit_parse(session_factory, att_id, data_dir)
        job = _wait_for_status(session_factory, job_id, {"failed"})
        assert job.status == "failed"
        assert job.attempts == 1  # 不可重试
        assert "不支持" in (job.last_error or "")
        parsed = _attachment_parsed(session_factory, att_id)
        assert parsed["status"] == "parse_failed"

    def test_retryable_error_retries_then_completes(self, session_factory, worker):
        attempts = {"n": 0}

        def flaky_handler(job, session, checkpoint, is_cancelled):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RetryableJobError("临时错误：外部服务暂不可用")
            return {"succeeded": True}

        worker.register_handler("echo", flaky_handler)
        job_id = submit_job(session_factory, "echo")
        job = _wait_for_status(session_factory, job_id, {"completed"})
        assert job.status == "completed"
        assert job.attempts == 2
        assert (job.checkpoint_json or {}).get("succeeded") is True

    def test_retryable_job_error_uses_backoff_queue(self, session_factory, worker):
        """临时错误：任务先回到 queued 且 next_attempt_at 在未来（退避）。"""
        calls = {"n": 0}

        def failing_handler(job, session, checkpoint, is_cancelled):
            calls["n"] += 1
            raise RetryableJobError("临时错误")

        worker.register_handler("echo", failing_handler)
        job_id = submit_job(session_factory, "echo")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with session_factory() as session:
                job = session.get(BackgroundJob, job_id)
                if job and job.status == "queued" and job.next_attempt_at is not None:
                    import datetime as dt
                    # SQLite 无时区列，SQLAlchemy 读回 naive；按 UTC 比较
                    scheduled = job.next_attempt_at.replace(tzinfo=dt.timezone.utc)
                    assert scheduled > dt.datetime.now(dt.timezone.utc)
                    return
            time.sleep(0.02)
        raise AssertionError("任务未进入退避队列（queued + next_attempt_at 未来）")

    def test_max_attempts_marks_failed(self, session_factory, worker):
        def always_retryable(job, session, checkpoint, is_cancelled):
            raise RetryableJobError("持续临时错误")

        worker.register_handler("echo", always_retryable)
        job_id = submit_job(session_factory, "echo")
        job = _wait_for_status(session_factory, job_id, {"failed"})
        assert job.status == "failed"
        assert "最大重试次数" in (job.last_error or "")

    def test_image_waits_ocr_not_completed(self, session_factory, data_dir, term, worker):
        from PIL import Image
        img_path = data_dir / "scan.png"
        Image.new("RGB", (300, 200), color="white").save(str(img_path))
        att_id = _create_attachment(
            session_factory, term, content=img_path.read_bytes(),
            original_name="scan.png", mime_type="image/png", data_dir=data_dir,
        )
        job_id = _submit_parse(session_factory, att_id, data_dir)
        job = _wait_for_status(session_factory, job_id, {"waiting_ocr"})
        assert job.status == "waiting_ocr"
        assert job.completed_at is None  # 不伪装成完全成功
        parsed = _attachment_parsed(session_factory, att_id)
        assert parsed["status"] == "pending_ocr"
        assert parsed["needs_ocr"] is True

    def test_cancel_not_overwritten_to_completed(self, session_factory, worker):
        cancel_seen = threading.Event()

        def cancellable_handler(job, session, checkpoint, is_cancelled):
            if is_cancelled():
                cancel_seen.set()
                return {"cancelled": True}
            return {"status": "pending_review"}  # 若未被取消，则会正常完成

        worker.register_handler("echo", cancellable_handler)
        # 注册一个慢任务以便在运行中取消
        slow_started = threading.Event()

        def slow_handler(job, session, checkpoint, is_cancelled):
            slow_started.set()
            while not is_cancelled():
                time.sleep(0.01)
            return {"cancelled": True}

        worker.register_handler("echo", slow_handler)
        job_id = submit_job(session_factory, "echo")
        assert slow_started.wait(timeout=2.0)
        assert worker.cancel_job(job_id) is True
        job = _wait_for_status(session_factory, job_id, {"cancelled"})
        assert job.status == "cancelled"
        # 等待足够时间，确认取消状态不会被后来覆盖为 completed
        time.sleep(0.5)
        with session_factory() as session:
            refreshed = session.get(BackgroundJob, job_id)
            assert refreshed.status == "cancelled"

    def test_parse_failed_checkpoint_never_completed(self, session_factory, worker):
        """防御兜底：handler 返回 parse_failed checkpoint 时任务必须 failed。"""

        def bad_handler(job, session, checkpoint, is_cancelled):
            return {"status": "parse_failed", "error": "不可恢复的内容错误（模拟）"}

        worker.register_handler("echo", bad_handler)
        job_id = submit_job(session_factory, "echo")
        job = _wait_for_status(session_factory, job_id, {"failed"})
        assert job.status == "failed"
        assert "不可恢复" in (job.last_error or "")