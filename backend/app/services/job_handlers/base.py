"""后台任务处理器注册 (U4-01)

将所有内置任务处理器注册到 JobWorker。

当前处理器：
- echo: 测试用，直接返回 checkpoint
- stats_refresh: 刷新考试摘要与逐题指标
- attachment_parse: 解析附件文件，提取文本 (U4-02)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...models.agent_entities import BackgroundJob
from ...models.entities import Exam

logger = logging.getLogger(__name__)


def echo_handler(
    job: BackgroundJob,
    session: Session,
    checkpoint: dict[str, Any],
    is_cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """Echo 处理器（测试用）。

    将 scope_json 复制到 checkpoint，标记完成。
    """
    checkpoint["echoed_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint["scope"] = job.scope_json
    return checkpoint


def stats_refresh_handler(
    job: BackgroundJob,
    session: Session,
    checkpoint: dict[str, Any],
    is_cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """重新计算考试摘要与逐题指标，并把结果写入任务 checkpoint。

    当前页面按需计算统计，没有独立缓存表，因此 checkpoint 是后台任务
    的可恢复结果；后续如增加缓存表仍可复用同一处理器边界。
    """
    exam_id = job.scope_json.get("exam_id")
    if exam_id is None:
        raise ValueError("stats_refresh 需要 scope.exam_id")

    if is_cancelled():
        return checkpoint

    from ...services.exams import exam_summary, question_metrics
    exam = session.get(Exam, exam_id)
    if exam is None:
        raise ValueError(f"考试 {exam_id} 不存在")
    summary = exam_summary(session, exam_id, term_id=exam.term_id)
    questions = question_metrics(session, exam_id, term_id=exam.term_id)
    checkpoint["refreshed_exam_id"] = exam_id
    checkpoint["summary"] = summary.model_dump() if hasattr(summary, "model_dump") else dict(summary)
    checkpoint["question_count"] = len(questions)
    checkpoint["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    return checkpoint


def school_sync_handler(
    job: BackgroundJob,
    session: Session,
    checkpoint: dict[str, Any],
    is_cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """执行一次学校成绩同步。当前使用统一合同，数据获取适配器可后续替换。"""
    from ...schemas.school_sync import SchoolSyncPayload
    from ...services.school_sync import apply_payload

    if is_cancelled():
        return {"cancelled": True}
    payload_data = (job.scope_json or {}).get("payload")
    if not payload_data:
        raise ValueError("school_sync 缺少 scope.payload")
    payload = SchoolSyncPayload.model_validate(payload_data)
    progress_callback = session.info.get("report_progress")
    run, summary = apply_payload(session, payload, progress_callback=progress_callback)
    checkpoint.update({
        "run_id": run.id,
        "summary": summary,
        "status": run.status,
        "phase": "completed",
        "phase_label": "同步完成",
        "message": "数据已写入本地数据库。",
        "processed": summary.get("students_written", 0),
        "total": len(payload.students),
        "items_written": summary.get("items_written", 0),
        "total_items": sum(len(student.item_scores) for student in payload.students),
        "conflicts": summary.get("conflicts", 0),
    })
    return checkpoint


def register_all_handlers(worker) -> None:
    """将所有内置处理器注册到 Worker。"""
    from .attachment_parse import attachment_parse_handler

    worker.register_handler("echo", echo_handler)
    worker.register_handler("stats_refresh", stats_refresh_handler)
    worker.register_handler("attachment_parse", attachment_parse_handler)
    worker.register_handler("school_sync", school_sync_handler)
    logger.info("已注册 %d 个内置任务处理器", 4)
