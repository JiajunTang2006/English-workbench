"""H0-2 回归测试：Harness SDK 安装与最小 Cordis 配置"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.config import AgentConfig
from backend.app.agent.runtime.base import (
    AgentRuntime, RunRequest, CancellationToken,
)
from backend.app.agent.orchestrator import OrchestratorRequest


class TestSDKImport:
    def test_sdk_import(self):
        from deepseek_harness import HarnessClient, HarnessConfig
        assert HarnessClient is not None
        assert HarnessConfig is not None

    def test_high_level_api_import(self):
        from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig, Session
        from deepseek_harness import RunResult as HarnessRunResult
        assert DeepSeekHarness is not None
        assert DeepSeekHarnessConfig is not None
        assert Session is not None
        assert HarnessRunResult is not None

    def test_runtime_bin_import(self):
        from deepseek_harness_runtime import (
            bundled_default_config_path, bundled_package_dir,
            resolve_bundled_launch_args,
        )
        assert callable(bundled_default_config_path)
        assert callable(bundled_package_dir)
        assert callable(resolve_bundled_launch_args)

    def test_harness_config_dataclass(self):
        from deepseek_harness import HarnessConfig
        config = HarnessConfig()
        assert hasattr(config, "runtime_bin")
        assert hasattr(config, "bridge_bin")
        assert hasattr(config, "env")
        assert hasattr(config, "request_timeout_seconds")

    def test_deepseek_harness_config_dataclass(self):
        from deepseek_harness import DeepSeekHarnessConfig
        config = DeepSeekHarnessConfig()
        assert hasattr(config, "provider")
        assert hasattr(config, "model")
        assert hasattr(config, "cordis")
        assert config.provider == "deepseek-official"


class TestCordisConfig:
    @pytest.fixture
    def cordis_path(self):
        return (
            Path(__file__).resolve().parent.parent
            / "app" / "agent" / "configs" / "teachmate.cordis.yml"
        )

    def test_config_file_exists(self, cordis_path):
        assert cordis_path.is_file()

    def test_config_has_required_plugins(self, cordis_path):
        content = cordis_path.read_text(encoding="utf-8")
        for p in ["sdk-jsonrpc-server", "agent-core", "llm-deepseek",
                   "sessions", "session-checkpoints"]:
            assert p in content, f"Missing: {p}"

    def test_config_excludes_dangerous_plugins(self, cordis_path):
        content = cordis_path.read_text(encoding="utf-8")
        dangerous = ["dsh-bash-local", "dsh-bash-sandbox", "dsh-subprocess-local",
                      "dsh-fs-local", "dsh-fs-sandbox", "dsh-subagent",
                      "dsh-tool-subagent", "dsh-tool-fs", "dsh-tool-workflow",
                      "dsh-tool-ralph"]
        for line in content.split("\n"):
            s = line.strip()
            if s.startswith("name:") and not s.startswith("#"):
                for d in dangerous:
                    if d in s:
                        pytest.fail(f"Dangerous plugin '{d}' active: {line}")

    def test_config_has_persona(self, cordis_path):
        content = cordis_path.read_text(encoding="utf-8")
        assert "TeachMate" in content or "teachmate" in content.lower()

    def test_config_has_compaction(self, cordis_path):
        content = cordis_path.read_text(encoding="utf-8")
        assert "compaction-basic" in content
        assert "dsh-compaction-basic" in content

    def test_config_has_token_meter(self, cordis_path):
        content = cordis_path.read_text(encoding="utf-8")
        assert "token-meter" in content
        assert "dsh-token-meter" in content

    def test_bundled_default_config_exists(self):
        from deepseek_harness_runtime import bundled_default_config_path
        assert bundled_default_config_path().is_file()


class TestHarnessRuntime:
    @pytest.fixture
    def config(self):
        return AgentConfig()

    @pytest.fixture
    def runtime(self, config):
        from backend.app.agent.runtime.harness import HarnessRuntime
        return HarnessRuntime(config)

    @pytest.fixture
    def orch_request(self):
        return OrchestratorRequest(
            teacher_id=0, capability_name="exam_analysis",
            scope={"exam_id": 1, "class_id": 1, "term_id": 1},
            user_message="test", session_id="h-1", db_session_id=1,
        )

    def test_implements_agent_runtime(self, runtime):
        assert isinstance(runtime, AgentRuntime)

    def test_runtime_version(self, runtime):
        assert runtime.runtime_version() == "harness-0.1.0"

    def test_health_returns_harness_info(self, runtime):
        h = runtime.health()
        assert h["runtime_kind"] == "harness"
        assert h["runtime_version"] == "harness-0.1.0"
        assert "cordis_config" in h
        assert "sdk_available" in h
        assert "runtime_binary_available" in h

    def test_health_sdk_available(self, runtime):
        assert runtime.health()["sdk_available"] is True

    def test_cordis_config_path(self, runtime):
        p = runtime.cordis_config_path
        assert p.is_file()
        assert "teachmate.cordis.yml" in str(p)

    def test_health_runtime_binary_status(self, runtime):
        assert isinstance(runtime.health()["runtime_binary_available"], bool)

    @pytest.mark.asyncio
    async def test_close_without_init(self, runtime):
        await runtime.close()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, runtime):
        assert await runtime.cancel("nope") is False

    @pytest.mark.asyncio
    async def test_cancel_before_start(self, runtime, orch_request):
        token = CancellationToken()
        token.cancel()
        req = RunRequest(orchestrator_request=orch_request, cancellation_token=token)
        r = await runtime.run(req)
        assert not r.success
        assert r.stop_reason == "cancelled"
        assert r.runtime_kind == "harness"

    @pytest.mark.asyncio
    async def test_run_fail_closed(self, runtime, orch_request):
        req = RunRequest(orchestrator_request=orch_request)
        r = await runtime.run(req)
        assert not r.success
        assert r.stop_reason == "error"
        assert r.runtime_kind == "harness"

    @pytest.mark.asyncio
    async def test_close_after_failed_init(self, runtime, orch_request):
        req = RunRequest(orchestrator_request=orch_request)
        await runtime.run(req)
        await runtime.close()

    @pytest.mark.asyncio
    async def test_confirm_returns_waiting(self, runtime):
        r = await runtime.confirm("x", {})
        assert r.needs_confirmation is True
        assert r.stop_reason == "waiting_confirmation"


class TestGetRuntimeHarness:
    def teardown_method(self):
        from backend.app.agent.runtime import clear_runtime
        clear_runtime()

    def test_harness_env(self):
        from backend.app.agent.runtime import get_runtime, HarnessRuntime
        with patch.dict(os.environ, {"AGENT_RUNTIME": "harness"}):
            rt = get_runtime()
            assert isinstance(rt, HarnessRuntime)

    def test_legacy_env_still_legacy(self):
        from backend.app.agent.runtime import get_runtime, set_runtime, LegacyRuntime
        mock_orch = MagicMock()
        set_runtime(LegacyRuntime(mock_orch, AgentConfig()))
        with patch.dict(os.environ, {"AGENT_RUNTIME": "legacy"}):
            rt = get_runtime()
            assert isinstance(rt, LegacyRuntime)
