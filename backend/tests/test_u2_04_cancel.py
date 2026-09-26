"""
test_u2_04_cancel — U2-04 真正取消运行测试

测试场景：
1. HarnessManager.cancel_session() — 通过 JSON-RPC session/cancel 取消
2. RunService.cancel() — 同时向 Harness 发送 session/cancel
3. cancel 端到端流程 — Harness 取消 + 本地 Task 取消 + 终态事件
4. 已终态运行不可取消
5. 无 harness_session_id 的运行仍可取消
6. Harness 不可用时不影响本地取消
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from backend.app.agent.runtime.harness_manager import (
    HarnessManager,
    HarnessConfig,
    get_harness_manager,
    set_harness_manager,
)
from backend.app.services.agent_runs.event_store import EventStore
from backend.app.services.agent_runs.repository import RunRepository
from backend.app.services.agent_runs.service import RunService
from backend.app.agent.event_types import (
    RUN_CANCELLED,
    RUN_REGISTERED,
    RUN_STARTED,
)


# ---------------------------------------------------------------------------
# HarnessManager.cancel_session 测试
# ---------------------------------------------------------------------------

class TestHarnessManagerCancelSession:
    """取消语义（真实协议无 session/cancel RPC）：本地标记 + 广播。"""

    @pytest.fixture
    def manager(self):
        m = HarnessManager()
        m._running = True
        m._loop = asyncio.new_event_loop()
        return m

    def test_cancel_session_registers_marker_and_broadcasts(self, manager):
        """cancel_session 注册取消标记并广播 cancelled 状态通知。"""
        received = []
        manager.register_event_callback(
            lambda method, params: received.append((method, params))
        )
        async def _test():
            result = await manager.cancel_session("tm-1-abc12345")
            assert result is True
            assert manager._is_session_cancelled("tm-1-abc12345")
        manager._loop.run_until_complete(_test())
        assert any(m == "session.status" and p.get("status") == "cancelled"
                   for m, p in received)

    def test_cancel_session_not_running(self):
        """HarnessManager 未运行时返回 False（不注册标记）。"""
        m = HarnessManager()
        m._running = False
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(m.cancel_session("tm-1-abc12345"))
            assert result is False
            assert m._is_session_cancelled("tm-1-abc12345") is False
        finally:
            loop.close()

    def test_cancelled_session_returns_True_idempotent(self, manager):
        """重复取消同一会话幂等（重复注册不报错）。"""
        async def _test():
            assert await manager.cancel_session("tm-d-1") is True
            assert await manager.cancel_session("tm-d-1") is True
        manager._loop.run_until_complete(_test())


# ---------------------------------------------------------------------------
# 全局单例测试
# ---------------------------------------------------------------------------

class TestHarnessManagerSingleton:
    def test_get_set_harness_manager(self):
        """get/set_harness_manager 单例访问。"""
        original = get_harness_manager()
        try:
            m = HarnessManager()
            set_harness_manager(m)
            assert get_harness_manager() is m
            set_harness_manager(None)
            assert get_harness_manager() is None
        finally:
            set_harness_manager(original)


# ---------------------------------------------------------------------------
# RunService.cancel 测试
# ---------------------------------------------------------------------------

class TestRunServiceCancel:
    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def event_store(self):
        return EventStore()

    @pytest.fixture
    def service(self, mock_db, event_store):
        return RunService(mock_db, event_store)

    def test_cancel_without_harness_session_id(self, service, event_store, mock_db):
        """没有 harness_session_id 的运行仍可取消。"""
        loop = asyncio.new_event_loop()
        try:
            mock_run = MagicMock()
            mock_run.harness_session_id = None
            mock_db.scalar.return_value = mock_run

            with patch.object(service._registry, 'cancel', new_callable=AsyncMock) as mock_cancel:
                mock_cancel.return_value = True
                result = loop.run_until_complete(service.cancel(1))
                assert result is True
                events = loop.run_until_complete(event_store.get_events(1))
                assert any(e.event_type == RUN_CANCELLED for e in events)
        finally:
            loop.close()

    def test_cancel_with_harness_session_id(self, service, event_store, mock_db):
        """有 harness_session_id 的运行会向 Harness 发送 session/cancel。"""
        loop = asyncio.new_event_loop()
        try:
            mock_run = MagicMock()
            mock_run.harness_session_id = "tm-1-abc12345"
            mock_run.status = "running"
            mock_db.scalar.return_value = mock_run

            with patch.object(service._registry, 'cancel', new_callable=AsyncMock) as mock_cancel, \
                 patch('backend.app.agent.runtime.harness_manager.get_harness_manager') as mock_get_mgr:
                mock_cancel.return_value = True
                mock_mgr = MagicMock()
                mock_mgr.is_running = True
                mock_mgr.cancel_session = AsyncMock(return_value=True)
                mock_get_mgr.return_value = mock_mgr

                result = loop.run_until_complete(service.cancel(1))
                assert result is True
                mock_mgr.cancel_session.assert_called_once_with("tm-1-abc12345")
                events = loop.run_until_complete(event_store.get_events(1))
                assert any(e.event_type == RUN_CANCELLED for e in events)
        finally:
            loop.close()

    def test_cancel_harness_unavailable_still_cancels_locally(
        self, service, event_store, mock_db
    ):
        """Harness 不可用时不影响本地取消。"""
        loop = asyncio.new_event_loop()
        try:
            mock_run = MagicMock()
            mock_run.harness_session_id = "tm-1-abc12345"
            mock_run.status = "running"
            mock_db.scalar.return_value = mock_run

            with patch.object(service._registry, 'cancel', new_callable=AsyncMock) as mock_cancel, \
                 patch('backend.app.agent.runtime.harness_manager.get_harness_manager') as mock_get_mgr:
                mock_cancel.return_value = True
                mock_get_mgr.return_value = None

                result = loop.run_until_complete(service.cancel(1))
                assert result is True
                events = loop.run_until_complete(event_store.get_events(1))
                assert any(e.event_type == RUN_CANCELLED for e in events)
        finally:
            loop.close()

    def test_cancel_already_terminal(self, service, event_store, mock_db):
        """已终态的运行取消返回 False。"""
        loop = asyncio.new_event_loop()
        try:
            mock_run = MagicMock()
            mock_run.harness_session_id = None
            mock_run.status = "completed"
            mock_db.scalar.return_value = mock_run

            with patch.object(service._registry, 'cancel', new_callable=AsyncMock) as mock_cancel:
                mock_cancel.return_value = False
                result = loop.run_until_complete(service.cancel(1))
                assert result is False
        finally:
            loop.close()

    def test_cancel_emits_terminal_event(self, service, event_store, mock_db):
        """取消成功后发射 run.cancelled 终态事件。"""
        loop = asyncio.new_event_loop()
        try:
            mock_run = MagicMock()
            mock_run.harness_session_id = None
            mock_run.status = "running"
            mock_db.scalar.return_value = mock_run

            with patch.object(service._registry, 'cancel', new_callable=AsyncMock) as mock_cancel:
                mock_cancel.return_value = True
                loop.run_until_complete(service.cancel(1))
                events = loop.run_until_complete(event_store.get_events(1))
                cancel_events = [e for e in events if e.event_type == RUN_CANCELLED]
                assert len(cancel_events) == 1
        finally:
            loop.close()

    def test_cancel_harness_exception_does_not_block_cancel(
        self, service, event_store, mock_db
    ):
        """Harness cancel_session 抛异常时不阻塞本地取消。"""
        loop = asyncio.new_event_loop()
        try:
            mock_run = MagicMock()
            mock_run.harness_session_id = "tm-1-abc12345"
            mock_run.status = "running"
            mock_db.scalar.return_value = mock_run

            with patch.object(service._registry, 'cancel', new_callable=AsyncMock) as mock_cancel, \
                 patch('backend.app.agent.runtime.harness_manager.get_harness_manager') as mock_get_mgr:
                mock_cancel.return_value = True
                mock_mgr = MagicMock()
                mock_mgr.is_running = True
                mock_mgr.cancel_session = AsyncMock(side_effect=RuntimeError("timeout"))
                mock_get_mgr.return_value = mock_mgr

                result = loop.run_until_complete(service.cancel(1))
                assert result is True
                events = loop.run_until_complete(event_store.get_events(1))
                assert any(e.event_type == RUN_CANCELLED for e in events)
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# 集成测试：cancel 完整流程
# ---------------------------------------------------------------------------

class TestCancelIntegration:
    def test_full_cancel_flow_with_harness(self):
        """完整取消流程：Harness session/cancel + 本地 Task.cancel + 终态事件。"""
        loop = asyncio.new_event_loop()
        try:
            db = MagicMock()
            event_store = EventStore()
            service = RunService(db, event_store)

            mock_run = MagicMock()
            mock_run.harness_session_id = "tm-1-abc12345"
            mock_run.status = "running"
            db.scalar.return_value = mock_run

            with patch.object(service._registry, 'cancel', new_callable=AsyncMock) as mock_cancel, \
                 patch('backend.app.agent.runtime.harness_manager.get_harness_manager') as mock_get_mgr:
                mock_cancel.return_value = True
                mock_mgr = MagicMock()
                mock_mgr.is_running = True
                mock_mgr.cancel_session = AsyncMock(return_value=True)
                mock_get_mgr.return_value = mock_mgr

                result = loop.run_until_complete(service.cancel(1))

                assert result is True
                mock_mgr.cancel_session.assert_called_once_with("tm-1-abc12345")
                mock_cancel.assert_called_once_with(1, persist=False)
                events = loop.run_until_complete(event_store.get_events(1))
                assert any(e.event_type == RUN_CANCELLED for e in events)
        finally:
            loop.close()

    def test_cancel_only_cancels_target_session(self):
        """取消只标记目标 session，不影响其他 session。"""
        loop = asyncio.new_event_loop()
        try:
            manager = HarnessManager()
            manager._running = True
            manager._loop = loop

            result_a = loop.run_until_complete(
                manager.cancel_session("tm-1-aaaa0000")
            )
            assert result_a is True
            assert manager._is_session_cancelled("tm-1-aaaa0000") is True

            # session B 未取消：不受影响，回合仍可执行
            assert manager._is_session_cancelled("tm-2-bbbb1111") is False
        finally:
            loop.close()

    def test_cancel_emits_run_cancelled_terminal_event(self):
        """取消后 run.cancelled 是终态事件。"""
        from backend.app.agent.event_types import TERMINAL_EVENT_TYPES

        assert RUN_CANCELLED in TERMINAL_EVENT_TYPES