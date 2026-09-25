"""运行服务 (U2-01)

运行状态机 + 业务编排。
职责：
1. 管理运行状态机的合法转换
2. 编排执行流程（validate → estimate → run → validate_output → terminal）
3. 发射运行事件到 EventStore
4. 协调 HarnessManager / Orchestrator / Repository

不直接操作 HTTP，不直接管理 asyncio.Task。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ...agent.event_types import (
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    RUN_DEGRADED,
    RUN_WAITING_CONFIRMATION,
    RUN_REGISTERED,
    RUN_IDLE,
    MODEL_STARTED,
    MODEL_DELTA,
    MODEL_COMPLETED,
    TOOL_STARTED,
    TOOL_COMPLETED,
    USAGE_UPDATED,
    VALIDATION_STARTED,
    VALIDATION_FAILED,
    PROGRESS_STEP_STARTED,
    PROGRESS_STEP_UPDATED,
    PROGRESS_STEP_COMPLETED,
    PROGRESS_HEARTBEAT,
)
from ...agent.task_registry import get_task_registry
from ...services.agent_runs.event_store import get_event_store, EventStore
from ...services.agent_runs.repository import RunRepository

logger = logging.getLogger(__name__)


class RunService:
    """
    运行服务：管理运行状态机和业务编排。

    使用方式：
        svc = RunService(db_session)
        await svc.register(run_id, session_id)
        await svc.transition(run_id, "validating")
        ...
        await svc.complete(run_id, result={...})
    """

    def __init__(self, db: Session, event_store: Optional[EventStore] = None):
        self._db = db
        self._repo = RunRepository(db)
        self._event_store = event_store or get_event_store()
        self._registry = get_task_registry()

    # ------------------------------------------------------------------
    # 注册 & 生命周期
    # ------------------------------------------------------------------

    async def _append_event(
        self, run_id: int, event_type: str, **data: Any
    ) -> None:
        """Append an event to memory and the durable event ledger."""
        await self._event_store.append_and_persist(
            run_id,
            event_type,
            db_session=self._db,
            **data,
        )

    async def register(self, run_id: int, session_id: int) -> None:
        """注册一个新运行。"""
        await self._registry.register(run_id, session_id, persist=False)
        await self._append_event(run_id, RUN_REGISTERED)
        self._db.commit()

    async def start(self, run_id: int, task: asyncio.Task) -> None:
        """绑定 asyncio.Task 并标记为 running。"""
        await self._registry.start(run_id, task, persist=False)
        await self._append_event(run_id, RUN_STARTED)
        self._db.commit()

    # ------------------------------------------------------------------
    # 状态转换（统一入口）
    # ------------------------------------------------------------------

    async def transition(
        self, run_id: int, new_status: str, **event_data: Any
    ) -> None:
        """
        执行状态转换并发射对应事件。
        如果转换不合法则抛出 ValueError。
        """
        self._repo.transition_status(run_id, new_status)
        self._db.commit()

        # 发射对应事件
        status_event_map = {
            "running": RUN_STARTED,
            "waiting_confirmation": RUN_WAITING_CONFIRMATION,
            "validating": VALIDATION_STARTED,
            "validating_output": VALIDATION_STARTED,
            "completed": RUN_COMPLETED,
            "degraded": RUN_DEGRADED,
            "failed": RUN_FAILED,
            "cancelled": RUN_CANCELLED,
        }
        event_type = status_event_map.get(new_status)
        if event_type:
            await self._append_event(
                run_id, event_type, status=new_status, **event_data
            )
            # 同步到 TaskRegistry
            if new_status in ("completed", "degraded"):
                await self._registry.complete(
                    run_id, status=new_status, persist=False
                )
            elif new_status == "failed":
                await self._registry.fail(
                    run_id, event_data.get("error", "运行失败"), persist=False
                )
            elif new_status == "cancelled":
                pass  # cancel 由 registry.cancel 处理
            elif new_status == "waiting_confirmation":
                await self._registry.set_status(run_id, "waiting_confirmation")

    # ------------------------------------------------------------------
    # 事件发射
    # ------------------------------------------------------------------

    async def emit_event(
        self, run_id: int, event_type: str, **data: Any
    ) -> None:
        """发射自定义事件。"""
        await self._append_event(run_id, event_type, **data)
        await self._registry.emit_event(
            run_id, event_type, persist=False, **data
        )

    async def emit_model_delta(self, run_id: int, delta: str) -> None:
        await self.emit_event(run_id, MODEL_DELTA, delta=delta)

    async def emit_tool_started(
        self, run_id: int, tool_name: str, call_id: str = ""
    ) -> None:
        await self.emit_event(
            run_id, TOOL_STARTED, tool_name=tool_name, call_id=call_id
        )

    async def emit_tool_completed(
        self,
        run_id: int,
        call_id: str = "",
        evidence_id: str = "",
        tool_name: str = "",
    ) -> None:
        data: dict[str, Any] = {"call_id": call_id}
        if tool_name:
            data["tool_name"] = str(tool_name)
            data["tool"] = str(tool_name)
        if evidence_id:
            data["evidence_id"] = evidence_id
        await self.emit_event(run_id, TOOL_COMPLETED, **data)

    async def emit_progress(
        self,
        run_id: int,
        step_id: str,
        status: str,
        title: str,
        summary: str = "",
        next_action: str = "",
        order: int = 0,
    ) -> None:
        """发射一条面向教师的语义进度事件。

        ``summary`` 和 ``next_action`` 必须是可公开的短文案；调用方不得把
        原始 prompt、模型思维链或未脱敏工具输出放入其中。
        """
        event_map = {
            "running": PROGRESS_STEP_STARTED,
            "updated": PROGRESS_STEP_UPDATED,
            "done": PROGRESS_STEP_COMPLETED,
        }
        event_type = event_map.get(status, PROGRESS_STEP_UPDATED)
        await self.emit_event(
            run_id,
            event_type,
            step_id=str(step_id),
            order=int(order or 0),
            title=str(title),
            summary=str(summary or ""),
            next_action=str(next_action or ""),
            status=status,
        )

    async def emit_progress_heartbeat(self, run_id: int, message: str = "") -> None:
        """发射轻量心跳，帮助前端区分“仍在工作”和“连接失活”。"""
        await self.emit_event(run_id, PROGRESS_HEARTBEAT, message=str(message or ""))

    async def emit_usage(
        self, run_id: int, input_tokens: int, output_tokens: int,
        total_tokens: int, cost_yuan: float = 0.0,
    ) -> None:
        await self.emit_event(
            run_id, USAGE_UPDATED,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_yuan=cost_yuan,
        )

    # ------------------------------------------------------------------
    # 终态
    # ------------------------------------------------------------------

    async def complete(
        self, run_id: int, result: dict[str, Any] | None = None
    ) -> None:
        """标记运行完成。"""
        await self.transition(run_id, "completed", result=result or {})

    async def degrade(
        self, run_id: int, reason: str, result: dict[str, Any] | None = None
    ) -> None:
        """标记运行降级完成。"""
        await self.transition(
            run_id, "degraded", reason=reason, result=result or {}
        )

    async def fail(
        self, run_id: int, error: str, result: dict[str, Any] | None = None
    ) -> None:
        """标记运行失败。"""
        await self.transition(
            run_id, "failed", error=error, result=result or {}
        )

    async def cancel(self, run_id: int) -> bool:
        """取消运行。

        1. 向 Harness 发送 session/cancel（如果运行有 harness_session_id）
        2. 取消本地 asyncio.Task
        3. 更新数据库状态为 cancelled
        4. 发射 run.cancelled 终态事件
        """
        # 尝试向 Harness 发送 session/cancel
        harness_session_id = self._get_harness_session_id(run_id)
        if harness_session_id:
            try:
                from backend.app.agent.runtime.harness_manager import get_harness_manager
                manager = get_harness_manager()
                if manager and manager.is_running:
                    await manager.cancel_session(harness_session_id)
            except Exception as e:
                logger.warning(
                    "Failed to send session/cancel to Harness for run %d: %s",
                    run_id, e,
                )

        # 取消本地 asyncio.Task
        cancelled = await self._registry.cancel(run_id, persist=False)
        if cancelled:
            try:
                self._repo.transition_status(run_id, "cancelled")
                self._db.commit()
            except ValueError:
                pass  # 可能已经终态
            await self._append_event(run_id, RUN_CANCELLED)
            self._db.commit()
        return cancelled

    def _get_harness_session_id(self, run_id: int) -> Optional[str]:
        """从数据库获取运行的 harness_session_id。"""
        try:
            run = self._repo.get_run(run_id)
            if run and hasattr(run, "harness_session_id"):
                return run.harness_session_id
        except Exception as e:
            logger.debug("Failed to get harness_session_id for run %d: %s", run_id, e)
        return None

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_events(self, run_id: int, after_seq: int = 0) -> list:
        """获取增量事件。"""
        return await self._event_store.get_events(run_id, after_seq)

    async def get_state(self, run_id: int):
        """获取运行状态。"""
        return await self._registry.get_state(run_id)

    async def is_cancelled(self, run_id: int) -> bool:
        return await self._registry.is_cancelled(run_id)

    @property
    def repository(self) -> RunRepository:
        return self._repo
