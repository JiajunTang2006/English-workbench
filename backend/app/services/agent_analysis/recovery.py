"""启动恢复、失败和重试服务。

.. deprecated::
    B2-05 后已停用暴露：本文件的 RecoveryService 会把可恢复的
    queued/running 运行与任务直接标记 failed，与正式恢复语义
    （services/agent_runs/recovery.py）相反。正式入口只有
    ``services.agent_runs.recovery.run_full_recovery``，由应用 factory
    在 lifespan 前调用；本文件仅作为历史实现保留，禁止新代码引用。

负责：
1. 应用启动时恢复中断的分析运行
2. 失败运行的分类和重试逻辑
3. 重启恢复测试支持
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.agent_entities import AnalysisRun, BackgroundJob
from .runs import RunService, TERMINAL_STATUSES

logger = logging.getLogger(__name__)

# 需要恢复的中间状态
INTERRUPTED_STATUSES = frozenset({"running", "queued", "validating", "estimating"})

# 最大重试次数
MAX_RETRIES = 3


class RecoveryService:
    """启动恢复与重试服务。"""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._run_service = RunService(db)

    def recover_interrupted_runs(self) -> list[AnalysisRun]:
        """恢复中断的分析运行。

        应用重启后，将处于中间状态的运行标记为 failed，
        记录错误信息，允许教师重试。
        """
        interrupted = list(self._db.scalars(
            select(AnalysisRun).where(
                AnalysisRun.status.in_(INTERRUPTED_STATUSES)
            )
        ))

        recovered: list[AnalysisRun] = []
        for run in interrupted:
            run.status = "failed"
            run.error_message = (
                f"运行在 {run.status} 状态时被中断（应用重启）"
            )
            run.completed_at = datetime.now(timezone.utc)
            recovered.append(run)

        if recovered:
            self._db.commit()
            logger.warning("恢复了 %d 个中断的分析运行", len(recovered))

        return recovered

    def recover_interrupted_jobs(self) -> list[BackgroundJob]:
        """恢复中断的后台任务。"""
        interrupted = list(self._db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.status.in_({"queued", "running"})
            )
        ))

        recovered: list[BackgroundJob] = []
        for job in interrupted:
            job.status = "failed"
            job.last_error = "任务在运行中被中断（应用重启）"
            job.completed_at = datetime.now(timezone.utc)
            recovered.append(job)

        if recovered:
            self._db.commit()
            logger.warning("恢复了 %d 个中断的后台任务", len(recovered))

        return recovered

    def can_retry(self, run_id: int) -> bool:
        """检查运行是否可以重试。"""
        run = self._run_service.get_run(run_id)
        if run is None:
            return False
        if run.status not in ("failed",):
            return False
        return (run.retry_count or 0) < MAX_RETRIES

    def prepare_retry(self, run_id: int) -> AnalysisRun | None:
        """准备重试：重置状态为 created，增加重试计数。"""
        if not self.can_retry(run_id):
            return None

        run = self._run_service.get_run(run_id)
        if run is None:
            return None

        run.retry_count = (run.retry_count or 0) + 1
        run.status = "created"
        run.error_message = None
        run.started_at = None
        run.completed_at = None
        self._db.commit()
        self._db.refresh(run)

        logger.info("准备重试运行 %d (第 %d 次)", run_id, run.retry_count)
        return run
