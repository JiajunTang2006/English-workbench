"""SQLite 后台任务 Worker (U4-01)

单进程、FIFO 后台任务执行器。使用数据库事务认领任务，防止重复执行。

特性：
- 默认单 Worker、FIFO
- 数据库事务认领任务，防止重复执行
- heartbeat、attempts、checkpoint、idempotency_key 生效
- 支持取消、重试、指数退避和最大次数
- 应用退出时停止接新任务，给当前任务有限收尾时间
- 启动时恢复 queued，审计 interrupted running

线程模型：
- Worker 运行在独立守护线程中
- 任务处理函数在 Worker 线程内同步执行
- 通过 threading.Event 进行协作式取消
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ..models.agent_entities import BackgroundJob

logger = logging.getLogger(__name__)

# --- 常量 ---

MAX_ATTEMPTS = 3
"""最大重试次数。超过后标记为 failed。"""

HEARTBEAT_INTERVAL_SECONDS = 5.0
"""heartbeat 更新间隔。"""

HEARTBEAT_TIMEOUT_SECONDS = 60.0
"""heartbeat 超时：超过此时间未更新的 running 任务视为中断。"""

BACKOFF_BASE_SECONDS = 2.0
"""指数退避基数。首次失败后的重试等待 BACKOFF_BASE * 2 秒。"""

BACKOFF_MAX_SECONDS = 120.0
"""退避最大等待时间。"""

SHUTDOWN_GRACE_SECONDS = 30.0
"""应用退出时给当前任务的收尾时间。"""

POLL_INTERVAL_SECONDS = 2.0
"""空闲轮询间隔。"""


class JobHandler(Protocol):
    """任务处理函数协议。"""

    def __call__(
        self,
        job: BackgroundJob,
        session: Session,
        checkpoint: dict[str, Any],
        is_cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        """执行任务，返回 checkpoint 更新。

        :param job: 后台任务对象
        :param session: 数据库会话（已开启事务）
        :param checkpoint: 上次的 checkpoint（用于断点续传）
        :param is_cancelled: 检查是否被取消的回调
        :return: 更新后的 checkpoint（会被保存到 job.checkpoint_json）
        """
        ...


class JobHandlerError(Exception):
    """后台任务处理器错误基类（S0-05）。

    处理器可通过抛出错误子类表达失败语义，Worker 据此决定重试或失败：
    - RetryableJobError：临时错误，进入指数退避队列重试；
    - PermanentJobError：不可恢复错误，任务直接 failed，不重试。
    """


class RetryableJobError(JobHandlerError):
    """临时错误：进入现有指数退避重试队列。"""


class PermanentJobError(JobHandlerError):
    """不可恢复错误：任务立即标记 failed，不进入重试。"""


class JobWorker:
    """SQLite 后台任务 Worker。

    单线程、FIFO 执行。通过数据库事务认领任务，
    保证同一任务不会被多个 Worker 重复执行。

    使用方式::

        worker = JobWorker(session_factory)
        worker.register_handler("pdf_extract", my_pdf_handler)
        worker.start()
        # ... 应用运行 ...
        worker.stop()
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        max_attempts: int = MAX_ATTEMPTS,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        shutdown_grace: float = SHUTDOWN_GRACE_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._max_attempts = max_attempts
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._shutdown_grace = shutdown_grace

        self._handlers: dict[str, JobHandler] = {}
        self._thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current_job_id: int | None = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._started = False

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        """注册任务处理函数。"""
        self._handlers[job_type] = handler
        logger.info("注册后台任务处理器: %s", job_type)

    def start(self) -> None:
        """启动 Worker 线程。"""
        if self._started:
            logger.warning("Worker 已启动，忽略重复调用")
            return
        self._started = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="job-worker", daemon=True,
        )
        self._thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="job-watchdog", daemon=True,
        )
        self._watchdog_thread.start()
        logger.info("后台任务 Worker 已启动（含心跳看门狗）")

    def stop(self, timeout: float | None = None) -> None:
        """停止 Worker 线程。

        设置停止标志，等待当前任务完成或超时。
        超时后强制取消当前任务。守护线程会被主进程回收。
        """
        if not self._started:
            return
        grace = timeout or self._shutdown_grace
        logger.info("正在停止 Worker，等待最多 %.1f 秒", grace)
        self._stop_event.set()
        # 不立即取消当前任务——给它 grace period 内正常完成
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=grace)
            # 如果 grace period 后仍在运行，强制取消
            if self._thread.is_alive():
                logger.warning(
                    "Worker 关闭超时，强制取消当前任务", stacklevel=2,
                )
                with self._lock:
                    self._cancel_event.set()
                self._thread.join(timeout=5.0)
        self._started = False
        logger.info("Worker 已停止")

    def cancel_job(self, job_id: int) -> bool:
        """请求取消指定任务。

        设置取消标志，任务处理函数通过 is_cancelled() 回调检测。
        取消是协作式的——任务需要主动检查。
        """
        with self._lock:
            if self._current_job_id == job_id:
                self._cancel_event.set()
                logger.info("已请求取消任务 #%d", job_id)
                return True
            else:
                # 任务不在运行中，直接在数据库标记取消
                with self._session_factory() as session:
                    job = session.get(BackgroundJob, job_id)
                    if job and job.status in ("queued", "running"):
                        job.status = "cancelled"
                        job.completed_at = datetime.now(timezone.utc)
                        session.commit()
                        logger.info("已直接取消未运行任务 #%d", job_id)
                        return True
                return False

    def get_status(self, job_id: int) -> dict[str, Any] | None:
        """获取任务状态。"""
        with self._session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job is None:
                return None
            return {
                "id": job.id,
                "job_type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "attempts": job.attempts,
                "last_error": job.last_error,
                "checkpoint": job.checkpoint_json,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }

    def report_progress(
        self,
        job_id: int,
        progress: float,
        *,
        checkpoint: dict[str, Any] | None = None,
        phase: str | None = None,
        phase_label: str | None = None,
        message: str | None = None,
    ) -> bool:
        """让长任务处理器把可见进度写入独立事务，避免前端只能看到 loading。"""
        with self._session_factory() as session:
            job = session.get(BackgroundJob, int(job_id))
            if job is None or job.status not in {"queued", "running"}:
                return False
            data = dict(job.checkpoint_json or {})
            if checkpoint:
                data.update(checkpoint)
            if phase is not None:
                # 任务可能在一次轮询间隔内完成多个阶段。保留阶段轨迹，
                # 让前端恢复状态和审计接口不会因只读到最终状态而丢失中间进度。
                history = data.get("phase_history")
                if not isinstance(history, list):
                    history = []
                last_phase = history[-1].get("phase") if history and isinstance(history[-1], dict) else None
                if last_phase != phase:
                    history.append({
                        "phase": phase,
                        "phase_label": phase_label,
                        "message": message,
                        "progress": max(0.0, min(1.0, float(progress))),
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                    })
                # 避免异常长任务无限增长，同时保留足够的阶段审计信息。
                data["phase_history"] = history[-50:]
            if phase is not None:
                data["phase"] = phase
            if phase_label is not None:
                data["phase_label"] = phase_label
            if message is not None:
                data["message"] = message
            job.progress = max(0.0, min(1.0, float(progress)))
            job.checkpoint_json = data
            job.updated_at = datetime.now(timezone.utc)
            session.commit()
            return True

    def retry_job(self, job_id: int) -> bool:
        """手动重试已失败/取消任务，清除退避时间但保留原始输入和审计记录。"""
        with self._session_factory() as session:
            job = session.get(BackgroundJob, int(job_id))
            if job is None or job.status not in {"failed", "cancelled"}:
                return False
            job.status = "queued"
            job.progress = 0.0
            job.attempts = 0
            job.next_attempt_at = None
            job.last_error = None
            job.completed_at = None
            job.updated_at = datetime.now(timezone.utc)
            checkpoint = dict(job.checkpoint_json or {})
            checkpoint.update({"phase": "queued", "phase_label": "等待后台任务开始", "message": "已重新提交，可安全离开页面。"})
            job.checkpoint_json = checkpoint
            session.commit()
            return True

    # --- 恢复逻辑 ---

    def recover_on_startup(self) -> dict[str, int]:
        """启动恢复（BackgroundJob）：queued 保持入队；中断的 running 重新入队。

        统一重试语义（B2-05 审查）：
        - 保留 next_attempt_at / attempts / max_attempts，不重置计数；
        - attempts < max_attempts 的中断任务 → 重回 queued（仍受退避约束：
          next_attempt_at 未到期时 Worker 不会拾取）；
        - 已达最大重试次数 → 标记 failed（保留 last_error 诊断）。

        :return: {"recovered": N, "interrupted": N}
        """
        now = datetime.now(timezone.utc)
        recovered = 0
        interrupted = 0

        with self._session_factory() as session:
            # 恢复 queued（保持入队，Worker 会自动拾取）
            queued = session.scalars(
                select(BackgroundJob).where(BackgroundJob.status == "queued")
            ).all()
            recovered = len(queued)

            # 中断的 running：按重试语义重新入队或失败
            running = session.scalars(
                select(BackgroundJob).where(BackgroundJob.status == "running")
            ).all()
            requeued = 0
            for job in running:
                if job.attempts < self._max_attempts:
                    # 重新入队：保留 attempts / next_attempt_at（真实指数退避）
                    job.status = "queued"
                    job.last_error = (
                        "任务在运行中被中断（应用重启），已重新入队"
                        + (f"；此前失败: {job.last_error}" if job.last_error else "")
                    )
                    job.updated_at = now
                    requeued += 1
                    logger.warning(
                        "恢复：任务 #%d 重新入队（attempts=%d/%d）",
                        job.id, job.attempts, self._max_attempts,
                    )
                else:
                    job.status = "failed"
                    job.last_error = (
                        "任务在运行中被中断（应用重启）且已达最大重试次数"
                    )
                    job.completed_at = now
                    job.updated_at = now
                    interrupted += 1
                    logger.warning(
                        "恢复：任务 #%d 已达最大重试次数，标记 failed", job.id,
                    )
            if requeued or interrupted:
                session.commit()

        logger.info(
            "启动恢复完成：queued=%d, requeued=%d, failed=%d",
            recovered, requeued, interrupted,
        )
        return {
            "recovered": recovered,
            "requeued": requeued,
            "interrupted": interrupted,
        }

    # --- 内部实现 ---

    def _run(self) -> None:
        """Worker 主循环。"""
        logger.info("Worker 主循环启动")

        while not self._stop_event.is_set():
            try:
                self._recover_zombie_jobs()
                job = self._claim_next_job()
                if job is None:
                    # 空闲等待
                    self._stop_event.wait(timeout=self._poll_interval)
                    continue

                self._execute_job(job)

            except Exception:
                logger.exception("Worker 主循环异常")

        logger.info("Worker 主循环退出")

    def _recover_zombie_jobs(self) -> int:
        """恢复心跳超时的僵尸 running 任务（P1 评审：heartbeat 无消费者）。

        单 Worker 模型下，running 且不是当前任务只可能是历史线程异常退出
        遗留的僵尸（heartbeat 线程已停，updated_at 停更）；以
        HEARTBEAT_TIMEOUT_SECONDS 为宽限（当前任务的 heartbeat 每 5 秒刷新
        updated_at，天然豁免；再显式跳过当前任务防双重执行），按
        recover_on_startup 的统一重试语义处理。
        """
        threshold = datetime.now(timezone.utc) - timedelta(
            seconds=HEARTBEAT_TIMEOUT_SECONDS,
        )
        recovered = 0
        with self._session_factory() as session:
            zombies = session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.status == "running",
                    BackgroundJob.updated_at < threshold,
                )
            ).all()
            current_job_id = self._current_job_id
            for job in zombies:
                if job.id == current_job_id:
                    continue
                now = datetime.now(timezone.utc)
                if job.attempts < self._max_attempts:
                    job.status = "queued"
                    job.next_attempt_at = now + timedelta(
                        seconds=compute_backoff_delay(job.attempts),
                    )
                    job.last_error = (
                        "任务心跳超时（运行线程中断），已自动重新入队"
                        + (f"；此前失败: {job.last_error}" if job.last_error else "")
                    )
                else:
                    job.status = "failed"
                    job.completed_at = now
                    job.last_error = (
                        "任务心跳超时（运行线程中断）且已达最大重试次数"
                    )
                job.updated_at = now
                recovered += 1
                logger.warning(
                    "恢复：僵尸任务 #%d 已处理（attempts=%d/%d）",
                    job.id, job.attempts, self._max_attempts,
                )
            if recovered:
                session.commit()
        return recovered

    def _watchdog_loop(self) -> None:
        """看门狗循环：周期性检测心跳超时的僵尸任务。"""
        while not self._stop_event.wait(timeout=HEARTBEAT_INTERVAL_SECONDS):
            try:
                self._recover_zombie_jobs()
            except Exception:
                logger.exception("看门狗僵尸任务恢复异常")

    def _claim_next_job(self) -> BackgroundJob | None:
        """认领下一个 queued 任务。

        使用数据库事务确保原子性：将 status 从 queued 改为 running，
        同时 attempts += 1，started_at 设为当前时间。
        如果事务失败（被其他 Worker 抢占），返回 None。

        指数退避：跳过 next_attempt_at 未到期的任务。
        """
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            # FIFO：按 id 升序取第一个 queued 且退避已到期的任务
            job = session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.status == "queued")
                .where(
                    (BackgroundJob.next_attempt_at == None)  # noqa: E711
                    | (BackgroundJob.next_attempt_at <= now)
                )
                .order_by(BackgroundJob.id)
                .limit(1)
            ).first()

            if job is None:
                return None

            # 原子认领：CAS (Compare-And-Swap)
            # 使用 UPDATE ... WHERE status='queued' 确保原子性
            result = session.execute(
                update(BackgroundJob)
                .where(
                    BackgroundJob.id == job.id,
                    BackgroundJob.status == "queued",
                )
                .values(
                    status="running",
                    attempts=BackgroundJob.attempts + 1,
                    started_at=now,
                    next_attempt_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount == 0:
                # 被其他 Worker 抢占
                return None

            session.commit()
            # 重新加载获取最新状态
            session.refresh(job)
            # 从 session 中分离，以便在 session 外使用
            session.expunge(job)
            return job

    def _execute_job(self, job: BackgroundJob) -> None:
        """执行单个任务。"""
        handler = self._handlers.get(job.job_type)
        if handler is None:
            logger.error("任务 #%d 类型 '%s' 无注册处理器", job.id, job.job_type)
            self._fail_job(job.id, f"无注册处理器: {job.job_type}")
            return

        # 设置当前任务和取消事件
        with self._lock:
            self._current_job_id = job.id
            self._cancel_event.clear()

        job_id = job.id
        checkpoint = dict(job.checkpoint_json or {})
        logger.info("开始执行任务 #%d (type=%s, attempts=%d)",
                     job_id, job.job_type, job.attempts)

        # 启动 heartbeat 线程
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, heartbeat_stop),
            name=f"heartbeat-{job_id}",
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            with self._session_factory() as session:
                session.info["report_progress"] = lambda progress, **kwargs: self.report_progress(
                    job_id, progress, **kwargs,
                )
                result_checkpoint = handler(
                    job=job,
                    session=session,
                    checkpoint=checkpoint,
                    is_cancelled=self._cancel_event.is_set,
                )
                session.commit()

            # 处理器返回的语义优先于事件竞态判断：
            # - cancelled=True：任务被协作式取消，绝不允许后续被覆盖为 completed；
            # - status == pending_ocr：附件等待 OCR，任务进入明确的待处理状态，
            #   不得伪装成完全成功；
            # - status == parse_failed：解析失败且不可重试（防御性兜底，
            #   正常情况下处理器会抛 PermanentJobError）。
            outcome = (result_checkpoint or {}).get("status")
            if self._cancel_event.is_set() or (result_checkpoint or {}).get("cancelled"):
                self._cancel_job(job_id, result_checkpoint)
            elif outcome == "pending_ocr":
                self._mark_waiting_ocr(job_id, result_checkpoint)
            elif outcome == "parse_failed":
                error_msg = str((result_checkpoint or {}).get("error") or "解析失败（不可重试）")
                self._fail_job(job_id, error_msg)
            else:
                self._complete_job(job_id, result_checkpoint)

        except PermanentJobError as exc:
            logger.error("任务 #%d 永久失败（不可重试）: %s", job_id, str(exc)[:300])
            self._fail_job(job_id, str(exc)[:2000])
        except RetryableJobError as exc:
            logger.info("任务 #%d 临时错误，进入退避重试: %s", job_id, str(exc)[:200])
            self._handle_failure(job_id, exc)
        except Exception as exc:
            logger.exception("任务 #%d 执行失败", job_id)
            self._handle_failure(job_id, exc)

        finally:
            heartbeat_stop.set()
            with self._lock:
                self._current_job_id = None
                self._cancel_event.clear()

    def _heartbeat_loop(self, job_id: int, stop_event: threading.Event) -> None:
        """定期更新任务的 updated_at 作为 heartbeat。"""
        while not stop_event.wait(timeout=self._heartbeat_interval):
            try:
                with self._session_factory() as session:
                    job = session.get(BackgroundJob, job_id)
                    if job is None or job.status != "running":
                        break
                    job.updated_at = datetime.now(timezone.utc)
                    session.commit()
            except Exception:
                logger.warning("heartbeat 更新失败 (job=%d)", job_id, exc_info=True)

    def _complete_job(self, job_id: int, checkpoint: dict[str, Any]) -> None:
        """标记任务完成。"""
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job is None:
                return
            job.status = "completed"
            job.progress = 1.0
            job.completed_at = now
            job.updated_at = now
            if checkpoint:
                # 处理器返回的最终 checkpoint 可能是在进度回调之前创建的；
                # 合并数据库中回调已记录的阶段轨迹，避免完成态覆盖审计信息。
                stored_checkpoint = dict(job.checkpoint_json or {})
                if stored_checkpoint.get("phase_history") and not checkpoint.get("phase_history"):
                    checkpoint = dict(checkpoint)
                    checkpoint["phase_history"] = stored_checkpoint["phase_history"]
                job.checkpoint_json = checkpoint
            session.commit()
        logger.info("任务 #%d 完成", job_id)

    def _mark_waiting_ocr(self, job_id: int, checkpoint: dict[str, Any]) -> None:
        """标记任务为等待 OCR 的明确待处理状态 (S0-05)。

        与 completed 明确区分：附件解析本身成功，但内容格式（图片）需要
        后续外部 OCR，任务保持可跟踪、可重试的待处理语义，不伪装成已完全成功。
        """
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job is None:
                return
            job.status = "waiting_ocr"
            job.progress = 1.0
            job.completed_at = None
            job.updated_at = now
            if checkpoint:
                job.checkpoint_json = checkpoint
            session.commit()
        logger.info("任务 #%d 等待 OCR（附件待处理）", job_id)

    def _cancel_job(self, job_id: int, checkpoint: dict[str, Any]) -> None:
        """标记任务取消。"""
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job is None:
                return
            job.status = "cancelled"
            job.completed_at = now
            job.updated_at = now
            if checkpoint:
                job.checkpoint_json = checkpoint
            session.commit()
        logger.info("任务 #%d 已取消", job_id)

    def _fail_job(self, job_id: int, error: str) -> None:
        """标记任务失败（不重试）。"""
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job is None:
                return
            job.status = "failed"
            job.last_error = error
            job.completed_at = now
            job.updated_at = now
            session.commit()
        logger.info("任务 #%d 失败: %s", job_id, error)

    def _handle_failure(self, job_id: int, exc: Exception) -> None:
        """处理任务失败：决定重试或标记失败。"""
        error_msg = str(exc)[:2000]  # 截断过长的错误信息
        now = datetime.now(timezone.utc)

        with self._session_factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job is None:
                return

            if job.attempts < self._max_attempts:
                # 重试：重新入队，设置 next_attempt_at 实现真实指数退避
                delay = compute_backoff_delay(job.attempts)
                job.status = "queued"
                job.next_attempt_at = now + timedelta(seconds=delay)
                job.last_error = f"第 {job.attempts} 次尝试失败: {error_msg}"
                job.updated_at = now
                session.commit()
                logger.info(
                    "任务 #%d 将在 %.1f 秒后重试 (attempts=%d/%d)",
                    job_id, delay, job.attempts, self._max_attempts,
                )
            else:
                # 达到最大重试次数，标记失败
                job.status = "failed"
                job.last_error = (
                    f"达到最大重试次数 ({self._max_attempts})，"
                    f"最后错误: {error_msg}"
                )
                job.completed_at = now
                job.updated_at = now
                session.commit()
                logger.warning(
                    "任务 #%d 达到最大重试次数，标记为 failed", job_id,
                )


# --- 全局 Worker 实例 ---

_global_worker: JobWorker | None = None


def set_global_worker(worker: JobWorker | None) -> None:
    """设置全局 Worker 实例。"""
    global _global_worker
    _global_worker = worker


def get_global_worker() -> JobWorker | None:
    """获取全局 Worker 实例。"""
    return _global_worker


# --- 任务提交 ---

def submit_job(
    session_factory: sessionmaker[Session],
    job_type: str,
    scope: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
    estimated_cost_yuan: float | None = None,
    retry_waiting_ocr: bool = False,
) -> int:
    """提交后台任务到队列。

    如果 idempotency_key 已存在且任务未完成，返回已有任务 ID。

    :return: 任务 ID
    """
    now = datetime.now(timezone.utc)

    with session_factory() as session:
        # 幂等性检查
        if idempotency_key:
            existing = session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.idempotency_key == idempotency_key,
                    BackgroundJob.status.in_(
                        ["queued", "running", "waiting_confirmation", "waiting_ocr"],
                    ),
                )
            ).first()
            if existing:
                if retry_waiting_ocr and existing.status == "waiting_ocr":
                    existing.status = "queued"
                    existing.progress = 0.0
                    existing.attempts = 0
                    existing.last_error = None
                    existing.checkpoint_json = {}
                    existing.next_attempt_at = None
                    existing.updated_at = now
                    session.commit()
                    logger.info("重新排队等待 OCR 的任务 #%d", existing.id)
                    return existing.id
                logger.info(
                    "幂等任务 '%s' 已存在 (job #%d)，跳过重复提交",
                    idempotency_key, existing.id,
                )
                return existing.id

        job = BackgroundJob(
            job_type=job_type,
            scope_json=scope or {},
            status="queued",
            progress=0.0,
            checkpoint_json={},
            estimated_cost_yuan=estimated_cost_yuan,
            idempotency_key=idempotency_key,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        logger.info("提交后台任务 #%d (type=%s)", job.id, job_type)
        return job.id


def compute_backoff_delay(attempts: int) -> float:
    """计算指数退避延迟。

    :param attempts: 已完成的尝试次数（第一次失败后为 1）
    :return: 延迟秒数

    attempts=0 -> BASE, attempts=1 -> BASE*2, attempts=2 -> BASE*4, ...
    """
    delay = BACKOFF_BASE_SECONDS * (2 ** max(0, attempts))
    return min(delay, BACKOFF_MAX_SECONDS)
