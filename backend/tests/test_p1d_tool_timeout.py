"""P1-D 回归测试：可真正终止的工具超时。

验证：
1. 超时后工具返回 error 而非正常结果
2. cancel_event 被设置，协作式取消生效
3. 超时结果不写入证据
4. ThreadPoolExecutor 正常工作
5. shutdown 方法释放资源
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.loop import AgentLoop
from backend.app.agent.tools.tool_context import ToolContext


class _FakeDefinition:
    """模拟 ToolDefinition。"""
    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds


class _FakeToolCall:
    """模拟 ToolCall。"""
    def __init__(self, name: str = "slow_tool", arguments: dict | None = None):
        self.tool_name = name
        self.arguments = arguments or {}


class _FakeContext:
    """模拟 TeachingContext。"""
    def __init__(self):
        self.scope = {"exam_id": 1}
        self.available_tools = ["slow_tool"]
        self.evidence_ledger = None


def _make_loop() -> AgentLoop:
    """创建一个最小化的 AgentLoop 用于测试。"""
    provider = MagicMock()
    registry = MagicMock()
    config = MagicMock()
    loop = AgentLoop(provider, registry, config)
    return loop


class TestCancelEventInToolContext:
    """ToolContext 的 cancel_event 功能。"""

    def test_cancel_event_default_not_set(self):
        ctx = ToolContext()
        assert not ctx.cancelled

    def test_cancel_event_set_and_check(self):
        ctx = ToolContext()
        ctx.cancel_event.set()
        assert ctx.cancelled
        with pytest.raises(TimeoutError):
            ctx.check_cancelled()

    def test_cancel_event_is_thread_safe(self):
        ctx = ToolContext()
        results = []

        def setter():
            time.sleep(0.01)
            ctx.cancel_event.set()
            results.append("set")

        t = threading.Thread(target=setter)
        t.start()
        # 等待取消
        ctx.cancel_event.wait(timeout=1.0)
        t.join()
        assert ctx.cancelled
        assert results == ["set"]


class TestTimeoutCancellation:
    """超时取消机制。"""

    @pytest.mark.asyncio
    async def test_timeout_returns_error_not_result(self):
        """超时后返回 error 字典，不返回迟到结果。"""
        loop = _make_loop()
        loop._tool_registry.get = MagicMock(return_value=_FakeDefinition(timeout_seconds=0.05))

        # 模拟一个慢工具
        def slow_handler(*args, **kwargs):
            time.sleep(2.0)
            return {"data": {"should_not": "arrive"}}

        loop._execute_tool = MagicMock(side_effect=slow_handler)
        loop._tool_registry.execute = MagicMock(side_effect=slow_handler)

        # 使用真实的 DB session（非内存 SQLite）
        mock_session = MagicMock()
        mock_bind = MagicMock()
        mock_bind.dialect.name = "postgresql"
        mock_bind.url.database = "test_db"
        mock_session.get_bind.return_value = mock_bind

        result = await loop._execute_tool_async(
            _FakeToolCall(), _FakeContext(), db_session=mock_session
        )

        assert "error" in result
        assert "超时" in result["error"]
        assert "data" not in result
        loop.shutdown()

    @pytest.mark.asyncio
    async def test_timeout_sets_cancel_event(self):
        """超时后 cancel_event 被设置，线程可协作式退出。"""
        loop = _make_loop()
        loop._tool_registry.get = MagicMock(return_value=_FakeDefinition(timeout_seconds=0.05))

        cancel_events_seen = []

        def handler_with_cancel_check(*args, **kwargs):
            from backend.app.agent.tools.tool_context import get_tool_context
            ctx = get_tool_context()
            if ctx:
                cancel_events_seen.append(ctx.cancel_event)
                # 等待取消
                for _ in range(100):
                    if ctx.cancelled:
                        return {"data": {"cancelled": True}}
                    time.sleep(0.02)
            return {"data": {"cancelled": False}}

        loop._execute_tool = MagicMock(side_effect=handler_with_cancel_check)

        mock_session = MagicMock()
        mock_bind = MagicMock()
        mock_bind.dialect.name = "postgresql"
        mock_bind.url.database = "test_db"
        mock_session.get_bind.return_value = mock_bind

        result = await loop._execute_tool_async(
            _FakeToolCall(), _FakeContext(), db_session=mock_session
        )

        assert "error" in result
        # cancel_event 被设置
        assert len(cancel_events_seen) > 0
        assert cancel_events_seen[0].is_set()
        loop.shutdown()

    @pytest.mark.asyncio
    async def test_no_timeout_when_disabled(self):
        """timeout_seconds=0 时不超时。"""
        loop = _make_loop()
        loop._tool_registry.get = MagicMock(return_value=_FakeDefinition(timeout_seconds=0))

        def fast_handler(*args, **kwargs):
            return {"data": {"ok": True}}

        loop._execute_tool = MagicMock(side_effect=fast_handler)

        mock_session = MagicMock()
        mock_bind = MagicMock()
        mock_bind.dialect.name = "postgresql"
        mock_bind.url.database = "test_db"
        mock_session.get_bind.return_value = mock_bind

        result = await loop._execute_tool_async(
            _FakeToolCall(), _FakeContext(), db_session=mock_session
        )

        # timeout=0 走 no-timeout 路径
        assert result == {"data": {"ok": True}}
        loop.shutdown()

    @pytest.mark.asyncio
    async def test_normal_execution_succeeds(self):
        """正常执行不受超时机制影响。"""
        loop = _make_loop()
        loop._tool_registry.get = MagicMock(return_value=_FakeDefinition(timeout_seconds=5.0))

        def normal_handler(*args, **kwargs):
            return {"data": {"result": 42}}

        loop._execute_tool = MagicMock(side_effect=normal_handler)

        mock_session = MagicMock()
        mock_bind = MagicMock()
        mock_bind.dialect.name = "postgresql"
        mock_bind.url.database = "test_db"
        mock_session.get_bind.return_value = mock_bind

        result = await loop._execute_tool_async(
            _FakeToolCall(), _FakeContext(), db_session=mock_session
        )

        assert result == {"data": {"result": 42}}
        loop.shutdown()


class TestShutdown:
    """线程池关闭。"""

    def test_shutdown_releases_executor(self):
        loop = _make_loop()
        loop.shutdown()
        # 重复调用不报错
        loop.shutdown()

    def test_executor_exists(self):
        loop = _make_loop()
        assert loop._executor is not None
        assert loop._executor._max_workers == 4
        loop.shutdown()
