"""H0-1 回归测试：AgentRuntime 接口与 LegacyRuntime 适配器。

验证：
1. LegacyRuntime 正确包装 AgentOrchestrator，不改变行为
2. 事件回调在关键节点被调用
3. 取消令牌工作正常
4. 健康检查返回正确信息
5. runtime_version 返回正确标识
6. get_runtime() 默认返回 LegacyRuntime
7. AGENT_RUNTIME=harness 在 H0 阶段 fail closed 到 legacy
8. 未知 AGENT_RUNTIME 值 fail closed 到 legacy
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from backend.app.agent.runtime.base import (
    AgentRuntime,
    RunRequest,
    RunResult,
    RuntimeEvent,
    CancellationToken,
)
from backend.app.agent.runtime.legacy import LegacyRuntime
from backend.app.agent.orchestrator import OrchestratorRequest, OrchestratorResponse
from backend.app.agent.config import AgentConfig


class TestCancellationToken:
    def test_default_not_cancelled(self):
        token = CancellationToken()
        assert not token.cancelled

    def test_cancel_sets_flag(self):
        token = CancellationToken()
        token.cancel()
        assert token.cancelled

    def test_wait_returns_true_after_cancel(self):
        token = CancellationToken()
        token.cancel()
        assert token.wait(timeout=0.1)

    def test_wait_timeout_returns_false(self):
        token = CancellationToken()
        assert not token.wait(timeout=0.05)

    def test_thread_safe(self):
        import threading
        import time
        token = CancellationToken()
        results = []

        def setter():
            time.sleep(0.01)
            token.cancel()
            results.append("set")

        t = threading.Thread(target=setter)
        t.start()
        token.wait(timeout=1.0)
        t.join()
        assert token.cancelled
        assert results == ["set"]


class TestLegacyRuntime:
    """LegacyRuntime 适配器测试。"""

    @pytest.fixture
    def mock_orchestrator(self):
        orch = MagicMock()
        orch.run = AsyncMock()
        orch.close = AsyncMock()
        return orch

    @pytest.fixture
    def config(self):
        return AgentConfig()

    @pytest.fixture
    def runtime(self, mock_orchestrator, config):
        return LegacyRuntime(mock_orchestrator, config)

    @pytest.fixture
    def orch_request(self):
        return OrchestratorRequest(
            teacher_id=0,
            capability_name="exam_analysis",
            scope={"exam_id": 1, "class_id": 1, "term_id": 1},
            user_message="分析本次考试",
            session_id="test-session-1",
            db_session_id=1,
        )

    @pytest.mark.asyncio
    async def test_run_success(self, runtime, mock_orchestrator, orch_request):
        """成功运行时返回正确的 RunResult。"""
        mock_orchestrator.run.return_value = OrchestratorResponse(
            success=True,
            session_id="test-session-1",
            answer="分析完成",
            structured_answer={"summary": "test"},
            cost_yuan=0.01,
            tokens_used=500,
            elapsed_ms=1000,
            stop_reason="completed",
        )
        request = RunRequest(orchestrator_request=orch_request)
        result = await runtime.run(request)
        assert result.success
        assert result.answer == "分析完成"
        assert result.stop_reason == "completed"
        assert result.runtime_kind == "legacy"
        assert result.runtime_version == "legacy-1.0.0"

    @pytest.mark.asyncio
    async def test_run_failure(self, runtime, mock_orchestrator, orch_request):
        """运行失败时返回正确的 RunResult。"""
        mock_orchestrator.run.return_value = OrchestratorResponse(
            success=False,
            session_id="test-session-1",
            answer="",
            error="分析失败",
            stop_reason="error",
        )
        request = RunRequest(orchestrator_request=orch_request)
        result = await runtime.run(request)
        assert not result.success
        assert result.error == "分析失败"

    @pytest.mark.asyncio
    async def test_run_needs_confirmation(self, runtime, mock_orchestrator, orch_request):
        """需要预算确认时返回正确标记。"""
        mock_orchestrator.run.return_value = OrchestratorResponse(
            success=False,
            session_id="test-session-1",
            answer="需要确认预算",
            needs_confirmation=True,
        )
        request = RunRequest(orchestrator_request=orch_request)
        result = await runtime.run(request)
        assert result.needs_confirmation

    @pytest.mark.asyncio
    async def test_event_callback_called(self, runtime, mock_orchestrator, orch_request):
        """事件回调在 run.started 和 run.completed 被调用。"""
        mock_orchestrator.run.return_value = OrchestratorResponse(
            success=True,
            session_id="test-session-1",
            answer="ok",
            stop_reason="completed",
        )
        events: list[RuntimeEvent] = []
        request = RunRequest(
            orchestrator_request=orch_request,
            event_sink=lambda ev: events.append(ev),
        )
        await runtime.run(request)
        event_types = [e.event_type for e in events]
        assert "run.started" in event_types
        assert "model.started" in event_types
        assert "run.completed" in event_types

    @pytest.mark.asyncio
    async def test_event_callback_on_failure(self, runtime, mock_orchestrator, orch_request):
        """运行失败时发送 run.failed 事件。"""
        mock_orchestrator.run.return_value = OrchestratorResponse(
            success=False,
            session_id="test-session-1",
            answer="",
            error="出错",
            stop_reason="error",
        )
        events: list[RuntimeEvent] = []
        request = RunRequest(
            orchestrator_request=orch_request,
            event_sink=lambda ev: events.append(ev),
        )
        await runtime.run(request)
        event_types = [e.event_type for e in events]
        assert "run.failed" in event_types

    @pytest.mark.asyncio
    async def test_cancel_before_start(self, runtime, mock_orchestrator, orch_request):
        """启动前取消返回 cancelled 状态。"""
        token = CancellationToken()
        token.cancel()
        request = RunRequest(
            orchestrator_request=orch_request,
            cancellation_token=token,
        )
        result = await runtime.run(request)
        assert not result.success
        assert result.stop_reason == "cancelled"
        mock_orchestrator.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_handling(self, runtime, mock_orchestrator, orch_request):
        """编排器抛异常时安全处理。"""
        mock_orchestrator.run.side_effect = RuntimeError("连接失败")
        request = RunRequest(orchestrator_request=orch_request)
        result = await runtime.run(request)
        assert not result.success
        assert "连接失败" in result.error

    @pytest.mark.asyncio
    async def test_cancelled_error_handling(self, runtime, mock_orchestrator, orch_request):
        """asyncio.CancelledError 处理。"""
        mock_orchestrator.run.side_effect = asyncio.CancelledError()
        request = RunRequest(orchestrator_request=orch_request)
        result = await runtime.run(request)
        assert not result.success
        assert result.stop_reason == "cancelled"

    @pytest.mark.asyncio
    async def test_close_delegates(self, runtime, mock_orchestrator):
        """close() 委托给编排器。"""
        await runtime.close()
        mock_orchestrator.close.assert_called_once()

    def test_health(self, runtime):
        """health() 返回正确信息。"""
        health = runtime.health()
        assert health["status"] == "healthy"
        assert health["runtime_kind"] == "legacy"
        assert health["runtime_version"] == "legacy-1.0.0"

    def test_runtime_version(self, runtime):
        """runtime_version() 返回正确标识。"""
        assert runtime.runtime_version() == "legacy-1.0.0"


class TestGetRuntimeFailClosed:
    """get_runtime() fail-closed 行为测试。"""

    def teardown_method(self):
        """每个测试后清除单例。"""
        from backend.app.agent.runtime import clear_runtime
        clear_runtime()

    def test_harness_fails_closed_to_legacy(self):
        """AGENT_RUNTIME=harness 在 H0 阶段 fail closed 到 legacy。"""
        import os
        from backend.app.agent.runtime import get_runtime, set_runtime, LegacyRuntime

        # 模拟有 orchestrator 可用
        mock_orch = MagicMock()
        config = AgentConfig()
        set_runtime(LegacyRuntime(mock_orch, config))

        with patch.dict(os.environ, {"AGENT_RUNTIME": "harness"}):
            runtime = get_runtime()
            assert isinstance(runtime, LegacyRuntime)

    def test_unknown_value_fails_closed(self):
        """未知 AGENT_RUNTIME 值 fail closed 到 legacy。"""
        import os
        from backend.app.agent.runtime import get_runtime, set_runtime, LegacyRuntime

        mock_orch = MagicMock()
        config = AgentConfig()
        set_runtime(LegacyRuntime(mock_orch, config))

        with patch.dict(os.environ, {"AGENT_RUNTIME": "unknown_value"}):
            runtime = get_runtime()
            assert isinstance(runtime, LegacyRuntime)

    def test_default_is_legacy(self):
        """不设置 AGENT_RUNTIME 时默认使用 legacy。"""
        import os
        from backend.app.agent.runtime import get_runtime, set_runtime, LegacyRuntime

        mock_orch = MagicMock()
        config = AgentConfig()
        set_runtime(LegacyRuntime(mock_orch, config))

        # 确保环境变量未设置
        env = {k: v for k, v in os.environ.items() if k != "AGENT_RUNTIME"}
        with patch.dict(os.environ, env, clear=True):
            runtime = get_runtime()
            assert isinstance(runtime, LegacyRuntime)
