"""轻量后台任务管理器。

正式的附件/解析任务使用持久化 ``JobWorker``；本模块为短任务和兼容调用方
提供进程内的排队、状态、取消和错误语义，避免保留一个假接口。
"""

from __future__ import annotations

import inspect
import logging
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class _ManagedJob:
    id: int
    job_type: str
    payload: dict[str, Any]
    status: str = "queued"
    progress: float = 0.0
    result: Any = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Future | None = None


class BackgroundJobManager:
    """进程内后台任务管理器（兼容门面）。"""

    def __init__(self, *, max_workers: int = 2) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._running: dict[int, _ManagedJob] = {}
        self._finished: OrderedDict[int, _ManagedJob] = OrderedDict()
        self._finished_limit = 200
        self._lock = threading.RLock()
        self._next_id = 1
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="background-job",
        )
        self._closed = False

    def register_handler(self, job_type: str, handler: Callable[..., Any]) -> None:
        if not job_type or not callable(handler):
            raise ValueError("job_type 和 handler 均不能为空")
        self._handlers[str(job_type)] = handler
        logger.info("注册后台任务处理器: %s", job_type)

    def submit(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        handler: Callable[..., Any] | None = None,
    ) -> int:
        """提交任务并立即返回本地任务 ID。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("后台任务管理器已关闭")
            task_handler = handler or self._handlers.get(job_type)
            if task_handler is None:
                raise ValueError(f"无注册任务处理器: {job_type}")
            job = _ManagedJob(
                id=self._next_id,
                job_type=str(job_type),
                payload=dict(payload or {}),
            )
            self._next_id += 1
            self._running[job.id] = job
            job.future = self._executor.submit(
                self._execute_job, job.id, job.job_type, job.payload, task_handler,
            )
            return job.id

    def get_status(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            job = self._running.get(int(job_id))
            if job is None:
                job = self._finished.get(int(job_id))
            if job is None:
                return None
            return {
                "id": job.id,
                "job_type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "result": job.result,
                "error": job.error,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }

    def cancel(self, job_id: int) -> bool:
        """取消 queued/running 任务；运行中的处理器需协作检查回调。"""
        with self._lock:
            job = self._running.get(int(job_id))
            if job is None or job.status in {"completed", "failed", "cancelled"}:
                return False
            job.cancel_event.set()
            if job.future is not None and job.future.cancel():
                job.status = "cancelled"
                job.completed_at = datetime.now(timezone.utc)
                self._archive_finished_locked(job)
            return True

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for job in self._running.values():
                if job.status in {"queued", "running"}:
                    job.cancel_event.set()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _execute_job(
        self,
        job_id: int,
        job_type: str,
        payload: dict[str, Any],
        handler: Callable[..., Any] | None = None,
    ) -> None:
        """在线程池中执行任务，所有结束路径都写入明确终态。"""
        with self._lock:
            job = self._running.get(job_id)
            if job is None or job.cancel_event.is_set():
                if job is not None:
                    job.status = "cancelled"
                    job.completed_at = datetime.now(timezone.utc)
                    self._archive_finished_locked(job)
                return
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            task_handler = handler or self._handlers.get(job_type)
        if task_handler is None:
            self._finish(job_id, status="failed", error=f"无注册任务处理器: {job_type}")
            return

        try:
            result = self._call_handler(task_handler, payload, job.cancel_event.is_set)
            with self._lock:
                job = self._running.get(job_id)
                if job is None:
                    return
                if job.cancel_event.is_set() or (isinstance(result, dict) and result.get("cancelled")):
                    job.status = "cancelled"
                else:
                    job.status = "completed"
                    job.progress = 1.0
                    job.result = result
                job.completed_at = datetime.now(timezone.utc)
                self._archive_finished_locked(job)
        except Exception as exc:
            logger.exception("后台任务 #%d 执行失败", job_id)
            self._finish(job_id, status="failed", error=str(exc)[:2000])

    @staticmethod
    def _call_handler(
        handler: Callable[..., Any],
        payload: dict[str, Any],
        is_cancelled: Callable[[], bool],
    ) -> Any:
        """兼容带/不带取消回调的处理器，并支持异步处理器。"""
        try:
            signature = inspect.signature(handler)
            accepts_kw = "is_cancelled" in signature.parameters
        except (TypeError, ValueError):
            accepts_kw = False
        result = handler(payload, is_cancelled=is_cancelled) if accepts_kw else handler(payload)
        if inspect.isawaitable(result):
            import asyncio
            return asyncio.run(result)
        return result

    def _finish(self, job_id: int, *, status: str, error: str | None = None) -> None:
        with self._lock:
            job = self._running.get(job_id)
            if job is None:
                return
            job.status = status
            job.error = error
            job.completed_at = datetime.now(timezone.utc)
            self._archive_finished_locked(job)

    def _archive_finished_locked(self, job: _ManagedJob) -> None:
        """将终态任务移出运行集合，并只保留最近一小段历史。"""
        self._running.pop(job.id, None)
        self._finished[job.id] = job
        self._finished.move_to_end(job.id)
        while len(self._finished) > self._finished_limit:
            self._finished.popitem(last=False)
