"""重启恢复服务 (U2-05)

在应用启动时扫描中断的运行和后台任务，按精细化策略恢复：

状态恢复策略：
1. queued → 保持 queued，由 Worker 拾取
2. waiting_confirmation → 保持等待，不自动付费
3. running 且无终态事件 → 标记 interrupted，根据 retry_count 决定是否自动重试
4. 已产生外部付费请求但结果未知 → 不静默重复调用，先标记 interrupted 等待教师确认
5. completed → 从 SQLite 重放报告和证据，不依赖内存
6. validating / estimating → 标记 interrupted，可自动重试

后台任务恢复策略：
1. queued → 保持 queued，由 Worker 拾取
2. running → 按 attempts 重新入队或标记 failed
3. waiting_confirmation → 保持等待

设计原则：
- 绝不静默重复外部付费调用
- completed 状态从 DB 重放，不依赖内存 EventStore
- 恢复操作全部记录日志
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...agent.event_types import (
    TERMINAL_EVENT_TYPES,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    RUN_DEGRADED,
    RUN_INTERRUPTED,
)
from ...models.agent_entities import (
    AnalysisRun,
    AnalysisRunEvent,
    BackgroundJob,
)
from ...services.agent_runs.repository import RunRepository

logger = logging.getLogger(__name__)

# 最大自动重试次数
MAX_AUTO_RETRY = 2

# 需要恢复的非终态运行状态
_RECOVERABLE_RUN_STATUSES = frozenset({
    "running",
    "queued",
    "validating",
    "estimating",
    "waiting_confirmation",
    "validating_output",
})

# 终态运行状态
_TERMINAL_RUN_STATUSES = frozenset({
    "completed",
    "degraded",
    "failed",
    "cancelled",
})


@dataclass
class RecoveryReport:
    """恢复操作报告。"""
    runs_examined: int = 0
    runs_requeued: int = 0
    runs_kept_waiting: int = 0
    runs_interrupted: int = 0
    runs_auto_retry: int = 0
    runs_needs_confirmation: int = 0
    runs_reconciled: int = 0
    jobs_examined: int = 0
    jobs_requeued: int = 0
    jobs_kept_waiting: int = 0
    jobs_failed: int = 0
    errors: list[str] = field(default_factory=list)
    interrupted_run_ids: list[int] = field(default_factory=list)
    needs_confirmation_run_ids: list[int] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"runs: examined={self.runs_examined}",
            f"requeued={self.runs_requeued}",
            f"kept_waiting={self.runs_kept_waiting}",
            f"interrupted={self.runs_interrupted}",
            f"auto_retry={self.runs_auto_retry}",
            f"needs_confirmation={self.runs_needs_confirmation}",
            f"reconciled={self.runs_reconciled}",
            f"jobs: examined={self.jobs_examined}",
            f"requeued={self.jobs_requeued}",
            f"kept_waiting={self.jobs_kept_waiting}",
            f"failed={self.jobs_failed}",
        ]
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return ", ".join(parts)


def _has_terminal_event(db: Session, run_id: int) -> bool:
    """检查运行是否已持久化了终态事件。"""
    stmt = (
        select(AnalysisRunEvent)
        .where(
            AnalysisRunEvent.run_id == run_id,
            AnalysisRunEvent.event_type.in_(
                [RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED, RUN_DEGRADED]
            ),
        )
        .limit(1)
    )
    return db.execute(stmt).scalar() is not None


def _has_external_cost(run: AnalysisRun) -> bool:
    """检查运行是否已产生外部付费请求。

    判断依据：
    - actual_cost_yuan > 0
    - 或 input_summary_json 中有 request_ids
    - 或 tool_calls_json 非空（意味着调用了外部工具）
    """
    if run.actual_cost_yuan and run.actual_cost_yuan > 0:
        return True
    summary = run.input_summary_json or {}
    if summary.get("request_ids"):
        return True
    if run.tool_calls_json:
        return True
    return False


def _get_terminal_event_type(db: Session, run_id: int) -> Optional[str]:
    """获取运行的终态事件类型（如果有多个取最后一个）。"""
    stmt = (
        select(AnalysisRunEvent)
        .where(
            AnalysisRunEvent.run_id == run_id,
            AnalysisRunEvent.event_type.in_(
                [RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED, RUN_DEGRADED]
            ),
        )
        .order_by(AnalysisRunEvent.seq.desc())
        .limit(1)
    )
    row = db.execute(stmt).scalar()
    return row.event_type if row else None


def _reconcile_from_terminal_event(
    db: Session, run: AnalysisRun, report: RecoveryReport
) -> None:
    """从持久化的终态事件恢复运行状态。

    如果数据库中有终态事件但 run.status 还是非终态，
    说明崩溃发生在事件持久化之后、状态更新之前。
    """
    terminal_type = _get_terminal_event_type(db, run.id)
    if terminal_type is None:
        return

    status_map = {
        RUN_COMPLETED: "completed",
        RUN_DEGRADED: "degraded",
        RUN_FAILED: "failed",
        RUN_CANCELLED: "cancelled",
    }
    new_status = status_map.get(terminal_type)
    if new_status and new_status != run.status:
        old_status = run.status
        RunRepository(db).transition_status(run.id, new_status)
        logger.info(
            "启动恢复：运行 %d 从终态事件 %s 恢复状态 %s -> %s",
            run.id, terminal_type, old_status, new_status,
        )
        report.runs_reconciled += 1


def recover_interrupted_runs(db: Session) -> RecoveryReport:
    """恢复中断的运行。

    在应用启动时调用。扫描所有非终态运行，按策略恢复。
    """
    report = RecoveryReport()

    try:
        interrupted_runs = db.scalars(
            select(AnalysisRun).where(
                AnalysisRun.status.in_(_RECOVERABLE_RUN_STATUSES)
            )
        ).all()
    except Exception as e:
        logger.warning("启动恢复：查询中断运行失败（表可能不存在）: %s", e)
        report.errors.append(str(e))
        return report

    report.runs_examined = len(interrupted_runs)

    for run in interrupted_runs:
        try:
            _recover_single_run(db, run, report)
        except Exception as e:
            logger.error(
                "启动恢复：恢复运行 %d 失败: %s", run.id, e, exc_info=True
            )
            report.errors.append(f"run {run.id}: {e}")

    if interrupted_runs:
        db.commit()
        logger.info("启动恢复：%s", report.summary())

    return report


def _recover_single_run(
    db: Session, run: AnalysisRun, report: RecoveryReport
) -> None:
    """恢复单个中断运行。"""
    run_id = run.id
    old_status = run.status

    # 检查是否已有终态事件（可能在崩溃前已完成但状态未更新）
    if _has_terminal_event(db, run_id):
        _reconcile_from_terminal_event(db, run, report)
        return

    has_external_cost = _has_external_cost(run)

    if old_status == "queued":
        # queued -> 保持 queued，Worker 会拾取
        logger.info(
            "启动恢复：运行 %d 保持 queued（等待 Worker 拾取）", run_id
        )
        report.runs_requeued += 1
        return

    if old_status == "waiting_confirmation":
        # waiting_confirmation -> 保持等待，不自动付费
        logger.info(
            "启动恢复：运行 %d 保持 waiting_confirmation（等待教师确认）",
            run_id,
        )
        report.runs_kept_waiting += 1
        return

    # running / validating / estimating / validating_output
    # 这些状态意味着运行被强制中断

    if has_external_cost:
        # 已产生外部付费请求但结果未知 -> 不静默重复，标记 interrupted 等待确认
        RunRepository(db).transition_status(run.id, "interrupted")
        run.error_message = (
            (run.error_message or "")
            + f" [启动恢复：运行在 {old_status} 状态被中断，"
            + "已产生外部付费请求，需教师确认是否重试]"
        )
        report.runs_interrupted += 1
        report.runs_needs_confirmation += 1
        report.needs_confirmation_run_ids.append(run_id)
        logger.warning(
            "启动恢复：运行 %d 标记 interrupted"
            "（已产生外部付费请求，需教师确认重试）",
            run_id,
        )
        return

    # 没有外部付费请求 -> 根据重试次数决定
    current_attempts = run.retry_count or 0
    if current_attempts < MAX_AUTO_RETRY:
        # 自动重试：interrupted -> queued
        RunRepository(db).transition_status(run.id, "interrupted")
        run.retry_count = current_attempts + 1
        run.error_message = (
            (run.error_message or "")
            + f" [启动恢复：运行在 {old_status} 状态被中断，"
            + f"自动重试 (attempt {current_attempts + 1}/{MAX_AUTO_RETRY})]"
        )
        report.runs_interrupted += 1
        report.runs_auto_retry += 1
        logger.info(
            "启动恢复：运行 %d 标记 interrupted -> queued"
            "（自动重试 attempt %d/%d）",
            run_id, current_attempts + 1, MAX_AUTO_RETRY,
        )
        # 直接转为 queued，让 Worker 拾取
        RunRepository(db).transition_status(run.id, "queued")
        report.runs_requeued += 1
    else:
        # 超过最大重试次数 -> 标记 interrupted，需教师确认
        RunRepository(db).transition_status(run.id, "interrupted")
        run.error_message = (
            (run.error_message or "")
            + f" [启动恢复：运行在 {old_status} 状态被中断，"
            + f"已重试 {current_attempts} 次，需教师确认是否继续重试]"
        )
        report.runs_interrupted += 1
        report.runs_needs_confirmation += 1
        report.needs_confirmation_run_ids.append(run_id)
        logger.warning(
            "启动恢复：运行 %d 标记 interrupted"
            "（已超过最大重试次数 %d，需教师确认）",
            run_id, MAX_AUTO_RETRY,
        )


def recover_interrupted_jobs(db: Session) -> RecoveryReport:
    """恢复中断的后台任务。

    在应用启动时调用。扫描所有非终态后台任务，按策略恢复。
    """
    report = RecoveryReport()

    _JOB_RECOVERABLE_STATUSES = frozenset({
        "queued",
        "running",
        "waiting_confirmation",
    })

    try:
        jobs = db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.status.in_(_JOB_RECOVERABLE_STATUSES)
            )
        ).all()
    except Exception as e:
        logger.warning("启动恢复：查询中断后台任务失败（表可能不存在）: %s", e)
        report.errors.append(str(e))
        return report

    report.jobs_examined = len(jobs)

    for job in jobs:
        try:
            _recover_single_job(db, job, report)
        except Exception as e:
            logger.error(
                "启动恢复：恢复后台任务 %d 失败: %s", job.id, e, exc_info=True
            )
            report.errors.append(f"job {job.id}: {e}")

    if jobs:
        db.commit()
        logger.info("启动恢复（后台任务）：%s", report.summary())

    return report


def _recover_single_job(
    db: Session, job: BackgroundJob, report: RecoveryReport
) -> None:
    """恢复单个中断后台任务。"""
    job_id = job.id
    old_status = job.status

    if old_status == "queued":
        # queued -> 保持 queued，Worker 会拾取
        logger.info(
            "启动恢复：后台任务 %d 保持 queued（等待 Worker 拾取）", job_id
        )
        report.jobs_requeued += 1
        return

    if old_status == "waiting_confirmation":
        # waiting_confirmation -> 保持等待
        logger.info(
            "启动恢复：后台任务 %d 保持 waiting_confirmation",
            job_id,
        )
        report.jobs_kept_waiting += 1
        return

    # running -> 保留 attempts/退避语义，未达上限则重新入队。
    # 与 JobWorker.recover_on_startup 保持同一入口语义，避免启动时先
    # 标记 failed 导致 Worker 再也扫描不到 running 任务。
    from ..job_worker import MAX_ATTEMPTS
    if job.attempts < MAX_ATTEMPTS:
        job.status = "queued"
        job.last_error = (
            (job.last_error or "")
            + " [启动恢复：任务在 running 状态被中断，已重新入队]"
        )
        job.updated_at = datetime.now(timezone.utc)
        report.jobs_requeued += 1
        logger.warning(
            "启动恢复：后台任务 %d 重新入队（attempts=%d/%d）",
            job_id, job.attempts, MAX_ATTEMPTS,
        )
        return
    job.status = "failed"
    job.last_error = (
        (job.last_error or "")
        + " [启动恢复：任务在 running 状态被中断且已达最大重试次数]"
    )
    job.completed_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    report.jobs_failed += 1
    logger.warning(
        "启动恢复：后台任务 %d 标记 failed（已达最大重试次数）", job_id
    )


def run_full_recovery(db: Session) -> RecoveryReport:
    """执行完整的启动恢复。

    合并运行恢复和后台任务恢复的报告。
    """
    logger.info("启动恢复：开始执行完整恢复...")

    runs_report = recover_interrupted_runs(db)
    jobs_report = recover_interrupted_jobs(db)

    # 合并报告
    merged = RecoveryReport()
    merged.runs_examined = runs_report.runs_examined
    merged.runs_requeued = runs_report.runs_requeued
    merged.runs_kept_waiting = runs_report.runs_kept_waiting
    merged.runs_interrupted = runs_report.runs_interrupted
    merged.runs_auto_retry = runs_report.runs_auto_retry
    merged.runs_needs_confirmation = runs_report.runs_needs_confirmation
    merged.runs_reconciled = runs_report.runs_reconciled
    merged.jobs_examined = jobs_report.jobs_examined
    merged.jobs_requeued = jobs_report.jobs_requeued
    merged.jobs_kept_waiting = jobs_report.jobs_kept_waiting
    merged.jobs_failed = jobs_report.jobs_failed
    merged.errors = runs_report.errors + jobs_report.errors
    merged.interrupted_run_ids = runs_report.interrupted_run_ids
    merged.needs_confirmation_run_ids = runs_report.needs_confirmation_run_ids

    logger.info("启动恢复：完成。%s", merged.summary())

    if merged.needs_confirmation_run_ids:
        logger.warning(
            "启动恢复：以下运行需要教师确认是否重试: %s",
            merged.needs_confirmation_run_ids,
        )

    return merged
