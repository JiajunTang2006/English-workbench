"""分析运行状态机与幂等管理。

AnalysisRun 状态流转：
    created -> validating -> estimating -> waiting_confirmation
            -> queued -> running -> completed / failed / cancelled
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.agent_entities import AnalysisRun

logger = logging.getLogger(__name__)

# 合法的状态值
VALID_STATUSES = frozenset({
    "created", "validating", "estimating", "waiting_confirmation",
    "queued", "running", "completed", "failed", "cancelled", "degraded",
})

# 终态（不可再转换）—— degraded 也是终态
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "degraded"})


class RunService:
    """分析运行管理服务。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_run(
        self,
        *,
        session_id: int | None = None,
        capability: str,
        term_id: int,
        class_id: int | None = None,
        exam_id: int | None = None,
        student_id: int | None = None,
        estimated_cost_yuan: float | None = None,
        estimated_tokens: int | None = None,
        rules_version: str | None = None,
        prompt_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> AnalysisRun:
        """创建分析运行记录。

        作用域通过独立列 (class_id/exam_id/student_id) 存储，不使用 scope_json。
        支持幂等：如 idempotency_key 已存在，返回已创建的 run。
        """
        from ..subjects import get_selected_subject
        subject_key = get_selected_subject(self._db).key
        # 幂等键只在当前学科内生效，避免旧运行被另一个学科复用。
        if idempotency_key:
            existing = self._db.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.input_summary_json["idempotency_key"].as_string()
                    == idempotency_key
                )
            ) if False else None  # idempotency_key 存储在 input_summary_json 中
            # 简化实现：通过 input_summary_json 的 idempotency_key 字段查找
            try:
                all_runs = self._db.scalars(
                    select(AnalysisRun).where(
                        AnalysisRun.capability == capability,
                        AnalysisRun.term_id == term_id,
                        AnalysisRun.subject_key == subject_key,
                    )
                ).all()
                for r in all_runs:
                    if r.input_summary_json.get("idempotency_key") == idempotency_key:
                        return r
            except Exception:
                pass

        run = AnalysisRun(
            session_id=session_id,
            capability=capability,
            term_id=term_id,
            subject_key=subject_key,
            class_id=class_id,
            exam_id=exam_id,
            student_id=student_id,
            status="created",
            estimated_cost_yuan=estimated_cost_yuan,
            estimated_tokens=estimated_tokens,
            rules_version=rules_version,
            prompt_version=prompt_version,
            input_summary_json={"idempotency_key": idempotency_key} if idempotency_key else {},
        )
        if session_id:
            from ...models.agent_entities import AgentSession
            conversation = self._db.get(AgentSession, session_id)
            if conversation and conversation.teaching_task_id:
                from ..teaching import require_task, build_task_context
                task = require_task(self._db, conversation.teaching_task_id, writable=True)
                run.input_summary_json = {**run.input_summary_json,
                    "teaching_task_id": task.id,
                    "teaching_context": build_task_context(self._db, task.id)}
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)
        logger.info("创建分析运行: id=%d, capability=%s", run.id, capability)
        return run

    def get_run(self, run_id: int) -> AnalysisRun | None:
        """获取运行记录。"""
        return self._db.scalar(
            select(AnalysisRun).where(AnalysisRun.id == run_id)
        )

    def update_status(
        self,
        run_id: int,
        status: str,
        *,
        actual_cost_yuan: float | None = None,
        actual_tokens: int | None = None,
        error_message: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> AnalysisRun | None:
        """更新运行状态。

        :param status: created/validating/estimating/waiting_confirmation/
                       queued/running/completed/failed/cancelled/degraded
        终态不可再转换。
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"非法状态: {status}")

        run = self.get_run(run_id)
        if run is None:
            return None

        # 终态不可再转换
        if run.status in TERMINAL_STATUSES and status != run.status:
            logger.warning("运行 %d 已处于终态 %s，不可转换为 %s", run_id, run.status, status)
            return run

        run.status = status

        if actual_cost_yuan is not None:
            run.actual_cost_yuan = actual_cost_yuan
        if actual_tokens is not None:
            run.actual_tokens = actual_tokens
        if error_message is not None:
            run.error_message = error_message
        if tool_calls is not None:
            run.tool_calls_json = tool_calls

        if status == "running" and run.started_at is None:
            run.started_at = datetime.now(timezone.utc)
        if status in TERMINAL_STATUSES:
            run.completed_at = datetime.now(timezone.utc)

        self._db.commit()
        self._db.refresh(run)
        return run

    def increment_retry(self, run_id: int) -> AnalysisRun | None:
        """增加重试次数。"""
        run = self.get_run(run_id)
        if run is None:
            return None
        run.retry_count = (run.retry_count or 0) + 1
        self._db.commit()
        self._db.refresh(run)
        return run

    def list_runs(
        self,
        *,
        session_id: int | None = None,
        capability: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AnalysisRun]:
        """分页列出运行记录。"""
        stmt = select(AnalysisRun)
        if session_id is not None:
            stmt = stmt.where(AnalysisRun.session_id == session_id)
        if capability is not None:
            stmt = stmt.where(AnalysisRun.capability == capability)
        if status is not None:
            stmt = stmt.where(AnalysisRun.status == status)
        stmt = stmt.order_by(AnalysisRun.created_at.desc()).limit(limit).offset(offset)
        return list(self._db.scalars(stmt))

    def get_run_cost(self, run_id: int) -> float:
        """获取单次运行的实际成本。"""
        run = self.get_run(run_id)
        return run.actual_cost_yuan or 0.0 if run else 0.0

    def get_run_tokens(self, run_id: int) -> int:
        """获取单次运行的实际 Token 数。"""
        run = self.get_run(run_id)
        return run.actual_tokens or 0 if run else 0
