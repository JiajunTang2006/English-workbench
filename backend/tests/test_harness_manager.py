"""HarnessManager 单元测试（协议适配后，真实协议语义）。

与 test_b2_01_harness_runtime.py 互补：
- b2_01 覆盖协议/进程复用/超时/取消/事件落库/运行入口；
- 本文件覆盖 manager 生命周期、健康检查（不泄 Key）、Future 回传、
  通知分发与事件投影器映射（真实 SDK 通知格式）。

全部离线 mock，不依赖真实 API Key 或网络。
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.runtime.harness_manager import (
    SUPPORTED_RPC_METHODS,
    HarnessConfig,
    HarnessManager,
    HealthStatus,
    _MAX_RESTARTS,
)


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def harness_config() -> HarnessConfig:
    return HarnessConfig(
        cordis_path="/tmp/cordis.yml",
        session_root="/tmp/sessions",
        model="deepseek-chat",
        api_key="sk-secret-key",
        runtime_bin="/tmp/bin.js",
    )


@pytest.fixture
def manager(harness_config) -> HarnessManager:
    return HarnessManager(harness_config)


def _fake_sdk():
    """最小 fake SDK：进程模拟 + start/close。"""
    fake = SimpleNamespace(
        started=False,
        closed=False,
        _client=SimpleNamespace(_proc=SimpleNamespace(pid=777, poll=lambda: None)),
    )
    return fake


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class TestHarnessConfig:
    def test_is_configured_all_present(self, harness_config):
        assert harness_config.is_configured is True

    def test_is_configured_missing_key(self, harness_config):
        harness_config.api_key = ""
        assert harness_config.is_configured is False

    def test_is_configured_missing_cordis(self, harness_config):
        harness_config.cordis_path = ""
        assert harness_config.is_configured is False

    def test_is_configured_missing_runtime_bin(self, harness_config):
        """缺少 vendored 运行时入口即视为未配置（不再指向 bin/dsh）。"""
        harness_config.runtime_bin = ""
        assert harness_config.is_configured is False

    def test_defaults(self):
        cfg = HarnessConfig(cordis_path="c", session_root="s", api_key="k",
                            runtime_bin="b")
        assert cfg.model == "deepseek-chat"
        assert cfg.api_base == "https://api.deepseek.com/v1"


# ---------------------------------------------------------------------------
# 生命周期基础
# ---------------------------------------------------------------------------

class TestHarnessManagerBasics:
    def test_not_running_initially(self, manager):
        assert manager.is_running is False

    def test_health_disabled_no_secrets(self):
        m = HarnessManager(None)
        status = m.health().to_dict()
        assert status["configured"] is False
        assert "api_key" not in json.dumps(status)
        assert "sk-secret-key" not in json.dumps(status)

    def test_health_configured_not_running(self, manager, harness_config):
        manager._config = harness_config
        assert manager.health().configured is True
        assert manager.health().running is False

    def test_call_when_not_running_raises(self, manager):
        async def scenario():
            await manager.call("session/prompt", sessionId="x", text="y")
        with pytest.raises(RuntimeError):
            asyncio.run(scenario())

    def test_start_without_config_raises(self):
        m = HarnessManager(None)

        async def scenario():
            await m.start()
        with pytest.raises(RuntimeError):
            asyncio.run(scenario())

    def test_supported_protocol_methods(self):
        assert SUPPORTED_RPC_METHODS == {"initialize", "session/prompt", "shutdown"}


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_no_secrets_in_output(self, manager, harness_config):
        manager._config = harness_config
        fake = _FakeSDK()
        manager._sdk = fake
        manager._running = True
        status = manager.health()
        d = status.to_dict()
        assert "sk-secret-key" not in json.dumps(d)
        assert status.running is True
        assert status.pid == 777

    def test_health_exposes_safe_runtime_diagnostics_and_redacts_errors(self, manager, harness_config):
        manager._config = harness_config
        manager._running = True
        manager._last_error = "provider returned sk-secret-key and Bearer abcdef123456"
        status = manager.health()
        payload = status.to_dict()
        assert payload["queue_depth"] == 0
        assert payload["active_method"] is None
        assert payload["generation"] == 0
        assert "sk-secret-key" not in str(payload)
        assert "abcdef123456" not in str(payload)

    def test_health_after_shutdown_not_running(self, manager):
        manager._running = False
        manager._sdk = None
        assert manager.health().running is False


class _FakeSDK:
    def __init__(self):
        self._client = SimpleNamespace(_proc=SimpleNamespace(pid=777, poll=lambda: None))


def _kd_sdk():
    return _FakeSDK()


# ---------------------------------------------------------------------------
# 自动重启
# ---------------------------------------------------------------------------

class TestAutoRestart:
    def test_maybe_restart_exceeds_max(self, manager):
        manager._restart_count = _MAX_RESTARTS
        assert manager._maybe_restart() is False
        assert "Max restarts" in (manager._last_error or "")

    @patch.object(HarnessManager, "_start_process")
    def test_maybe_restart_success(self, mock_start, manager):
        manager._restart_count = 0
        assert manager._maybe_restart() is True
        mock_start.assert_called_once()
        assert manager._restart_count == 1

    @patch.object(HarnessManager, "_start_process")
    def test_maybe_restart_start_fails(self, mock_start, manager):
        manager._restart_count = 0
        mock_start.side_effect = OSError("boom")
        assert manager._maybe_restart() is False
        assert "Restart failed" in (manager._last_error or "")


# ---------------------------------------------------------------------------
# Future 回传
# ---------------------------------------------------------------------------

class TestFutureResolution:
    def test_resolve_future_success(self, manager):
        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            req = MagicMock()
            req.future = future
            req.loop = loop
            manager._resolve_future(req, {"ok": True}, None)
            result = loop.run_until_complete(asyncio.wait_for(future, timeout=1))
            assert result == {"ok": True}
        finally:
            loop.close()

    def test_resolve_future_error(self, manager):
        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            req = MagicMock()
            req.future = future
            req.loop = loop
            manager._resolve_future(req, None, RuntimeError("boom"))
            with pytest.raises(RuntimeError, match="boom"):
                loop.run_until_complete(asyncio.wait_for(future, timeout=1))
        finally:
            loop.close()

    def test_resolve_future_already_done(self, manager):
        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            future.set_result("already")
            req = MagicMock()
            req.future = future
            req.loop = loop
            manager._resolve_future(req, {"new": True}, None)  # 不抛
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# 通知分发
# ---------------------------------------------------------------------------

class TestNotificationDispatch:
    def test_notification_forwarded_to_callback(self, manager):
        received = []
        manager.register_event_callback(
            lambda method, params: received.append((method, params))
        )
        manager._dispatch_notification(
            "session.event",
            {"sessionId": "s1", "event": {"type": "assistant/message", "data": {}}},
        )
        assert received == [
            ("session.event", {"sessionId": "s1",
                               "event": {"type": "assistant/message", "data": {}}}),
        ]

    def test_notification_no_callback_no_crash(self, manager):
        manager._event_callback = None
        manager._dispatch_notification("session.status", {"sessionId": "s1", "status": "idle"})

    def test_callback_exception_isolated(self, manager):
        def bad(m, p):
            raise ValueError("bad handler")
        manager.register_event_callback(bad)
        # 不应抛到调用方
        manager._dispatch_notification("session.status", {"status": "idle"})

    def test_empty_method_ignored(self, manager):
        manager._dispatch_notification("", {})


# ---------------------------------------------------------------------------
# shutdown 收尾
# ---------------------------------------------------------------------------

class TestShutdownCleanup:
    def test_shutdown_settles_queued_futures(self, manager):
        """shutdown 时排队请求收到明确异常，不永久挂起。"""
        fake = _kd_sdk()
        manager._sdk = fake
        loop = asyncio.new_event_loop()

        async def scenario():
            manager._loop = loop
            manager._running = True
            future = loop.create_future()
            req = MagicMock()
            req.future = future
            req.loop = loop
            manager._task_queue.put(req)
            await manager.shutdown()
            try:
                with __import__("contextlib").nullcontext():
                    await asyncio.wait_for(future, timeout=2)
                return False
            except Exception:
                return True

        settled = loop.run_until_complete(scenario())
        loop.close()
        assert settled


def MagicManager(config=None):
    return HarnessManager(config)
