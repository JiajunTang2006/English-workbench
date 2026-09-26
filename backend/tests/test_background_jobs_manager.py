from __future__ import annotations

import threading
import time

from backend.app.services.background_jobs import BackgroundJobManager


def _wait_for(manager: BackgroundJobManager, job_id: int, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = manager.get_status(job_id)
        if current and current["status"] == status:
            return current
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {status}")


def test_background_manager_runs_registered_handler_and_reports_result():
    manager = BackgroundJobManager(max_workers=1)
    try:
        manager.register_handler("echo", lambda payload: {"value": payload["value"]})
        job_id = manager.submit("echo", {"value": 3})
        status = _wait_for(manager, job_id, "completed")
        assert status["result"] == {"value": 3}
        assert status["progress"] == 1.0
    finally:
        manager.close()


def test_background_manager_supports_cooperative_cancel():
    manager = BackgroundJobManager(max_workers=1)
    started = threading.Event()
    try:
        def handler(_payload, *, is_cancelled):
            started.set()
            while not is_cancelled():
                time.sleep(0.01)
            return {"cancelled": True}

        job_id = manager.submit("slow", handler=handler)
        assert started.wait(1.0)
        assert manager.cancel(job_id) is True
        status = _wait_for(manager, job_id, "cancelled")
        assert status["error"] is None
    finally:
        manager.close()

