"""异步运行任务注册表

管理后台运行的 Agent 分析任务，支持：
- 注册和追踪运行状态
- 事件队列（SSE 或轮询消费）
- 取消运行
- 运行结果缓存

线程安全：所有操作通过 asyncio.Lock 保护。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 注册表软上限：超过后在新注册时机会性清理旧终态运行（P0 评审：
# _runs 是全局单例且每个 RunState 带完整事件列表，桌面应用长期驻留
# 必须有内存护栏）。
_RUNS_SOFT_LIMIT = 100
# 非终态运行视为进程内僵尸的阈值：正常执行不会持续数小时。
_STALE_RUNNING_SECONDS = 6 * 3600


@dataclass
class RunEvent:
    """运行事件。"""
    event_type: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: dict[str, Any] = field(default_factory=dict)
    # EventStore 持久化后的全局游标；没有数据库时回退到列表序号。
    seq: int | None = None

    def to_sse(self) -> str:
        """格式化为 SSE 行。"""
        import json
        payload = json.dumps(
            {
                "event": self.event_type,
                "timestamp": self.timestamp,
                "data": self.data,
                **({"seq": self.seq} if self.seq is not None else {}),
            },
            ensure_ascii=False,
        )
        event_id = f"id: {self.seq}\n" if self.seq is not None else ""
        return f"{event_id}data: {payload}\n\n"


@dataclass
class RunState:
    """运行状态。"""
    run_id: int
    session_id: int
    status: str = "queued"  # queued / running / waiting_confirmation / completed / failed / cancelled / degraded
    events: list[RunEvent] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task | None = None
    _cancelled: bool = False
    # monotonic 时间戳：清理逻辑判断非终态运行是否为进程内僵尸。
    created_at: float = field(default_factory=time.monotonic)

    # 终态集合（含 degraded）
    _TERMINAL = frozenset({"completed", "failed", "cancelled", "degraded"})

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def is_terminal(self) -> bool:
        """是否处于终态。"""
        return self.status in self._TERMINAL

    def add_event(self, event_type: str, **data: Any) -> RunEvent:
        ev = RunEvent(event_type=event_type, data=data)
        self.events.append(ev)
        return ev

    def latest_events_after(self, index: int) -> list[RunEvent]:
        """返回 index 之后的事件（用于轮询增量）。"""
        if any(event.seq is not None for event in self.events):
            return [
                event for position, event in enumerate(self.events, start=1)
                if (event.seq or position) > index
            ]
        if index >= len(self.events):
            return []
        return self.events[index:]

    @property
    def next_after(self) -> int:
        """下一次轮询的 after 游标（= 当前事件总数）。"""
        return max(
            [event.seq for event in self.events if event.seq is not None]
            or [len(self.events)]
        )


class TaskRegistry:
    """全局任务注册表。

    管理所有活跃和已完成的异步运行。
    """

    def __init__(self) -> None:
        self._runs: dict[int, RunState] = {}
        self._lock = asyncio.Lock()

    async def _persist_event(
        self, event_run_id: int, event_type: str, **data: Any
    ):
        """Persist a registry event when the application DB is available."""
        from ..database import get_global_session_factory
        from ..services.agent_runs.event_store import get_event_store

        factory = get_global_session_factory()
        if factory is None:
            return None
        with factory() as db_session:
            persisted_data = dict(data)
            persisted_data.pop("run_id", None)
            persisted = await get_event_store().append_and_persist(
                event_run_id,
                event_type,
                db_session=db_session,
                **persisted_data,
            )
            db_session.commit()
            return persisted

    async def register(
        self, run_id: int, session_id: int, *, persist: bool = True
    ) -> RunState:
        """注册一个新运行（幂等：活跃中重复注册不覆盖，防重复调度）。

        若该 run 已注册且未处于终态，直接返回现有状态，保留其
        task/status —— 防止恢复调度重复执行同一运行（B2-05 审查）。
        """
        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None and not existing.is_terminal:
                return existing
            state = RunState(run_id=run_id, session_id=session_id)
            self._runs[run_id] = state
            event = state.add_event("run.registered", run_id=run_id)
        if persist:
            persisted = await self._persist_event(run_id, "run.registered", run_id=run_id)
            if persisted is not None:
                event.seq = persisted.seq
        # 内存护栏（P0 评审）：_runs 全局单例、每个 RunState 带完整事件列表，
        # 桌面应用长期驻留必须有清理。放在锁外避免与 cleanup_old 重入死锁。
        if len(self._runs) > _RUNS_SOFT_LIMIT:
            await self.cleanup_old()
        return state

    async def start(
        self, run_id: int, task: asyncio.Task, *, persist: bool = True
    ) -> None:
        """绑定 asyncio.Task 到运行。"""
        emitted = False
        async with self._lock:
            state = self._runs.get(run_id)
            if state:
                state.task = task
                state.status = "running"
                event = state.add_event("run.started", run_id=run_id)
                emitted = True
        if persist and emitted:
            persisted = await self._persist_event(run_id, "run.started", run_id=run_id)
            if persisted is not None:
                event.seq = persisted.seq

    async def complete(
        self,
        run_id: int,
        result: dict[str, Any] | None = None,
        *,
        status: str = "completed",
        persist: bool = True,
    ) -> None:
        """标记运行完成；允许显式保留 degraded 终态。"""
        if status not in {"completed", "degraded"}:
            raise ValueError(f"complete() 不支持状态: {status}")
        async with self._lock:
            state = self._runs.get(run_id)
            if state:
                state.status = status
                state.result = result
                event = state.add_event("run.completed", run_id=run_id, status=status)
        if persist:
            persisted = await self._persist_event(
                run_id, "run.completed", run_id=run_id, status=status,
            )
            if persisted is not None and state is not None:
                event.seq = persisted.seq

    async def fail(
        self,
        run_id: int,
        error: str,
        *,
        result: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> None:
        """标记运行失败。"""
        async with self._lock:
            state = self._runs.get(run_id)
            if state:
                state.status = "failed"
                state.error = error
                if result is not None:
                    state.result = result
                event = state.add_event("run.failed", run_id=run_id, error=error)
        if persist:
            persisted = await self._persist_event(
                run_id, "run.failed", run_id=run_id, error=error,
            )
            if persisted is not None and state is not None:
                event.seq = persisted.seq

    async def cancel(self, run_id: int, *, persist: bool = True, message: str | None = None) -> bool:
        """取消运行。返回是否成功取消。

        如果任务已完成或已取消，返回 False。
        可选 ``message`` 写入 run.cancelled 事件数据（本地 cancel / 上一次运行残留时附诊断）。
        """
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return False
            if state.is_terminal:
                return False
            state._cancelled = True
            event = state.add_event("run.cancelled", run_id=run_id, **({"message": message} if message else {}))
            if state.task and not state.task.done():
                state.task.cancel()
            state.status = "cancelled"
        if persist:
            payload = {"run_id": run_id}
            if message:
                payload["message"] = message
            persisted = await self._persist_event(run_id, "run.cancelled", **payload)
            if persisted is not None:
                event.seq = persisted.seq
        return True

    async def get_state(self, run_id: int) -> RunState | None:
        """获取运行状态。"""
        async with self._lock:
            return self._runs.get(run_id)

    async def get_events(self, run_id: int, after: int = 0) -> list[RunEvent]:
        """获取运行事件（增量）。"""
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return []
            return state.latest_events_after(after)

    async def is_cancelled(self, run_id: int) -> bool:
        """检查运行是否已被取消。"""
        async with self._lock:
            state = self._runs.get(run_id)
            return state.cancelled if state else False

    async def emit_event(
        self,
        run_id: int,
        event_type: str,
        *,
        persist: bool = True,
        **data: Any,
    ) -> None:
        """向运行添加并持久化自定义事件。"""
        emitted = False
        async with self._lock:
            state = self._runs.get(run_id)
            if state:
                event = state.add_event(event_type, **data)
                emitted = True
        if emitted and persist:
            persisted = await self._persist_event(run_id, event_type, **data)
            if persisted is not None:
                event.seq = persisted.seq

    async def set_status(self, run_id: int, status: str) -> None:
        """设置运行状态（不触发终态事件）。"""
        async with self._lock:
            state = self._runs.get(run_id)
            if state:
                state.status = status

    async def cleanup_old(
        self,
        max_entries: int = _RUNS_SOFT_LIMIT,
        *,
        stale_running_seconds: float = _STALE_RUNNING_SECONDS,
    ) -> None:
        """清理旧运行，防止 _runs 只增不减（P0 评审）。

        - 僵尸非终态：running/waiting_confirmation 停留超过
          ``stale_running_seconds``（正常执行不会持续数小时）视为进程内
          僵尸，标记 failed 后允许移除——否则最老一批卡在非终态时
          一个都删不掉；
        - 数量护栏：超过 ``max_entries`` 后从最老开始移除**终态**运行；
        - 仅操作注册表内存（事件为内存副本），不触碰数据库持久化事件。
        """
        async with self._lock:
            now = time.monotonic()

            # 1) 僵尸非终态：超时强制失败（含事件留痕，便于轮询侧感知）
            stale = [
                state for state in self._runs.values()
                if not state.is_terminal
                and now - state.created_at > stale_running_seconds
            ]
            for state in stale:
                state.status = "failed"
                state.error = (
                    f"运行停留超过 {int(stale_running_seconds)} 秒未到达终态，"
                    "已被注册表回收"
                )
                state.add_event(
                    "run.failed",
                    run_id=state.run_id,
                    reason=state.error,
                )
                logger.warning(
                    "注册表回收僵尸运行 %s（停留超时，标记 failed）",
                    state.run_id,
                )

            # 2) 数量护栏：从最老开始移除终态运行
            if len(self._runs) <= max_entries:
                return
            remove_count = len(self._runs) - max_entries
            terminal_ids = [
                rid for rid in sorted(self._runs)
                if self._runs[rid].is_terminal
            ]
            for rid in terminal_ids[:remove_count]:
                del self._runs[rid]


# 全局单例
_task_registry: TaskRegistry | None = None


def get_task_registry() -> TaskRegistry:
    """获取全局任务注册表单例。"""
    global _task_registry
    if _task_registry is None:
        _task_registry = TaskRegistry()
    return _task_registry
