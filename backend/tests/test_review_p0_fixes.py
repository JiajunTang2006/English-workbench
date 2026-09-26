"""评审 P0 修复回归：SSE 事件游标（全局 seq）与注册表内存清理"""

from __future__ import annotations

import asyncio
import time

from backend.app.agent.task_registry import RunEvent, TaskRegistry
from backend.app.routers.agent import _advance_event_cursor


# ---------------------------------------------------------------------------
# Fix 1：SSE 游标按全局 seq 推进（跨 run 并发写入时 seq 跳跃）
# ---------------------------------------------------------------------------

def test_advance_cursor_uses_global_seq():
    # 评审复现场景：游标落后于全局最新 seq 时，不得重复推送
    assert _advance_event_cursor(102, RunEvent(event_type="x", data={}, seq=107)) == 107
    # 未持久化事件（seq=None）退化为本地序号推进
    assert _advance_event_cursor(102, RunEvent(event_type="x", data={}, seq=None)) == 103
    # seq 异常回退不得把游标往回拨
    assert _advance_event_cursor(109, RunEvent(event_type="x", data={}, seq=105)) == 109


def test_registry_events_after_global_seq_gap():
    """跨 run 并发写入（seq 交错跳跃）时，消费端按 seq 取增量、不重复。"""

    async def scenario():
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        await registry.register(2, 1, persist=False)
        for seq in range(1, 8):
            run_id = 1 if seq % 2 else 2  # 交替写入两个 run，制造全局 seq 跳跃
            event = registry._runs[run_id].add_event("step", n=seq)
            event.seq = seq

        # run 1 的消费端：游标 2（已消费 seq=1）之后，只有 seq 3/5/7 属于自己
        events = registry._runs[1].latest_events_after(2)
        assert [e.seq for e in events] == [3, 5, 7]
        # 消费端若错误地用“条数 +1”推进（2→3），会再次取到 seq 4/5/6/7 中的
        # 重复事件——这正是评审复现的缺陷，此处锁定正确语义。

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Fix 2：注册表内存护栏（cleanup_old 接入调用点 + 僵尸非终态强制失败）
# ---------------------------------------------------------------------------

def test_cleanup_old_force_fails_stale_running():
    """卡在非终态的最老运行不再阻塞清理：超时强制失败后允许移除。"""

    async def scenario():
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        await registry.register(2, 1, persist=False)
        stale_state = registry._runs[1]
        stale_state.status = "running"
        stale_state.created_at = time.monotonic() - 10_000  # 停留远超阈值

        await registry.cleanup_old(max_entries=1, stale_running_seconds=60)

        assert stale_state.status == "failed"
        assert "回收" in (stale_state.error or "")
        assert 1 not in registry._runs, "强制失败后应允许移除"
        assert registry._runs[2].is_terminal is False  # 新鲜非终态不受影响

    asyncio.run(scenario())


def test_fresh_running_not_evicted_by_count_guard():
    """数量护栏只移除终态；新鲜的非终态运行（可能正在执行）不动。"""

    async def scenario():
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        registry._runs[1].status = "completed"
        await registry.register(2, 1, persist=False)
        registry._runs[2].status = "running"  # 新鲜非终态

        await registry.cleanup_old(max_entries=1)

        assert 2 in registry._runs, "正在执行的新鲜运行不得被数量护栏移除"
        assert 1 not in registry._runs

    asyncio.run(scenario())


def test_count_guard_skips_old_running_and_removes_later_terminal_runs():
    """最老记录仍在运行时，后面的终态记录仍应被清理，不能让注册表超限。"""

    async def scenario():
        registry = TaskRegistry()
        await registry.register(1, 1, persist=False)
        registry._runs[1].status = "running"
        for run_id in (2, 3):
            await registry.register(run_id, 1, persist=False)
            registry._runs[run_id].status = "completed"

        await registry.cleanup_old(max_entries=1)

        assert list(registry._runs) == [1]

    asyncio.run(scenario())


def test_register_triggers_cleanup_over_soft_limit():
    """超过软上限后，register 自身触发清理（评审 P0-2 的调用点）。"""

    async def scenario():
        registry = TaskRegistry()
        for run_id in range(1, 103):
            state = await registry.register(run_id, 1, persist=False)
            state.status = "completed"  # 制造终态堆积
        assert len(registry._runs) <= 100, "register 应机会性清理旧终态运行"

    asyncio.run(scenario())
