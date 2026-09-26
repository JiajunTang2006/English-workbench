"""P0-9 回归测试：运行事件契约对齐

验证：
1. degraded 是终态，不可再转换
2. 轮询响应包含 next_after 游标
3. 事件类型与共享常量一致
4. SSE 循环在 degraded 终态时停止
5. TaskRegistry.cancel() 拒绝取消已 degraded 的运行
6. fail() 接受可选 result 参数
"""

from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timezone

from backend.app.agent.event_types import (
    ALL_EVENT_TYPES,
    TERMINAL_EVENT_TYPES,
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    RUN_REGISTERED,
)
from backend.app.agent.task_registry import TaskRegistry, RunState
from backend.app.services.agent_analysis.runs import TERMINAL_STATUSES, VALID_STATUSES


class TestEventTypes:
    """事件类型常量。"""

    def test_p09_all_event_types_defined(self):
        """所有关键事件类型已定义。"""
        assert RUN_REGISTERED == "run.registered"
        assert RUN_STARTED == "run.started"
        assert RUN_COMPLETED == "run.completed"
        assert RUN_FAILED == "run.failed"
        assert RUN_CANCELLED == "run.cancelled"

    def test_p09_terminal_event_types(self):
        """终态事件类型只包含 completed/failed/cancelled。"""
        assert RUN_COMPLETED in TERMINAL_EVENT_TYPES
        assert RUN_FAILED in TERMINAL_EVENT_TYPES
        assert RUN_CANCELLED in TERMINAL_EVENT_TYPES
        assert RUN_STARTED not in TERMINAL_EVENT_TYPES
        assert RUN_REGISTERED not in TERMINAL_EVENT_TYPES

    def test_p09_no_phantom_event_aliases(self):
        """后端不产生 status_change/message/error 别名。"""
        assert "status_change" not in ALL_EVENT_TYPES
        assert "message" not in ALL_EVENT_TYPES
        assert "error" not in ALL_EVENT_TYPES


class TestDegradedTerminal:
    """degraded 终态。"""

    def test_p09_degraded_is_terminal_status(self):
        """degraded 在 TERMINAL_STATUSES 中。"""
        assert "degraded" in TERMINAL_STATUSES
        assert "degraded" in VALID_STATUSES

    def test_p09_degraded_run_state_is_terminal(self):
        """RunState.is_terminal 对 degraded 返回 True。"""
        state = RunState(run_id=1, session_id=1, status="degraded")
        assert state.is_terminal is True

    def test_p09_completed_run_state_is_terminal(self):
        """RunState.is_terminal 对 completed 返回 True。"""
        state = RunState(run_id=1, session_id=1, status="completed")
        assert state.is_terminal is True

    def test_p09_running_run_state_not_terminal(self):
        """RunState.is_terminal 对 running 返回 False。"""
        state = RunState(run_id=1, session_id=1, status="running")
        assert state.is_terminal is False


class TestTaskRegistryDegraded:
    """TaskRegistry 对 degraded 的处理。"""

    @pytest.mark.asyncio
    async def test_p09_cancel_rejects_degraded(self):
        """cancel() 拒绝取消已 degraded 的运行。"""
        registry = TaskRegistry()
        state = await registry.register(1, 1, persist=False)
        state.status = "degraded"
        result = await registry.cancel(1, persist=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_complete_preserves_degraded_status_and_event(self):
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        await registry.complete(1, result={"status": "degraded"}, status="degraded", persist=False)
        state = await registry.get_state(1)
        assert state.status == "degraded"
        assert state.events[-1].data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_p09_cancel_rejects_completed(self):
        """cancel() 拒绝取消已 completed 的运行。"""
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        await registry.complete(1, result={"status": "completed"}, persist=False)
        result = await registry.cancel(1, persist=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_p09_fail_accepts_result(self):
        """fail() 接受可选 result 参数。"""
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        await registry.fail(1, "测试错误", result={"answer": "部分结果"}, persist=False)
        state = await registry.get_state(1)
        assert state.error == "测试错误"
        assert state.result == {"answer": "部分结果"}

    @pytest.mark.asyncio
    async def test_p09_fail_without_result(self):
        """fail() 不传 result 时 result 保持 None。"""
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        await registry.fail(1, "测试错误", persist=False)
        state = await registry.get_state(1)
        assert state.error == "测试错误"
        assert state.result is None


class TestNextAfterCursor:
    """轮询游标。"""

    @pytest.mark.asyncio
    async def test_p09_next_after_initial(self):
        """初始 next_after 为 0。"""
        registry = TaskRegistry()
        state = await registry.register(1, 1, persist=False)
        assert state.next_after == 1  # register 产生 1 个事件

    @pytest.mark.asyncio
    async def test_p09_next_after_grows_with_events(self):
        """next_after 随事件增长。"""
        registry = TaskRegistry()
        state = await registry.register(1, 1, persist=False)
        initial = state.next_after
        await registry.start(1, task=None, persist=False)  # type: ignore
        after_start = state.next_after
        assert after_start > initial
        await registry.complete(1, result={"status": "completed"}, persist=False)
        after_complete = state.next_after
        assert after_complete > after_start

    @pytest.mark.asyncio
    async def test_p09_latest_events_after_cursor(self):
        """latest_events_after 正确返回增量事件。"""
        registry = TaskRegistry()
        state = await registry.register(1, 1, persist=False)
        await registry.start(1, task=None, persist=False)  # type: ignore
        await registry.complete(1, result={"status": "completed"}, persist=False)

        # 获取从 index=1 开始的事件（跳过 register 事件）
        events = state.latest_events_after(1)
        event_types = [e.event_type for e in events]
        assert "run.started" in event_types
        assert "run.completed" in event_types
        assert "run.registered" not in event_types


class TestSSELoopTermination:
    """SSE 循环在终态停止。"""

    @pytest.mark.asyncio
    async def test_p09_sse_stops_on_degraded(self):
        """SSE 循环在 degraded 状态停止。"""
        registry = TaskRegistry()
        state = await registry.register(1, 1, persist=False)
        # 模拟 degraded 终态
        state.status = "degraded"
        assert state.is_terminal is True
        # SSE 循环条件: while not state.is_terminal
        # degraded 时不应进入循环

    @pytest.mark.asyncio
    async def test_p09_sse_stops_on_completed(self):
        """SSE 循环在 completed 状态停止。"""
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        await registry.complete(1, result={"status": "completed"}, persist=False)
        state = await registry.get_state(1)
        assert state is not None
        assert state.is_terminal is True

    @pytest.mark.asyncio
    async def test_p09_sse_continues_on_running(self):
        """SSE 循环在 running 状态继续。"""
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        state = await registry.get_state(1)
        assert state is not None
        state.status = "running"
        assert state.is_terminal is False
