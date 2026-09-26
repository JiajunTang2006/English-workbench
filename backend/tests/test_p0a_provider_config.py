"""P0-A: 统一 Provider 配置来源回归测试。

验证：
1. set_runtime_config / get_runtime_config / clear_runtime_config 正确工作
2. get_agent_config() 在有运行时覆盖时返回覆盖配置
3. get_agent_config() 在无覆盖时从环境变量构建
4. switch_provider 持久化配置（全链路共用）
5. switch_provider 失败时原子回滚
"""
from __future__ import annotations

import os
from dataclasses import replace
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.agent.config import (
    AgentConfig,
    set_runtime_config,
    get_runtime_config,
    clear_runtime_config,
    get_agent_config,
    _build_config_from_env,
)
from backend.app.auth import TOKEN


AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class TestRuntimeConfigOverride:
    """测试运行时配置覆盖机制。"""

    def teardown_method(self):
        """每个测试后清除运行时覆盖，避免影响其他测试。"""
        clear_runtime_config()

    def test_set_and_get_runtime_config(self):
        """set_runtime_config 设置后，get_runtime_config 返回同一对象。"""
        cfg = replace(AgentConfig(), text_provider="anthropic")
        set_runtime_config(cfg)
        assert get_runtime_config() is cfg

    def test_clear_runtime_config(self):
        """clear_runtime_config 后，get_runtime_config 返回 None。"""
        cfg = replace(AgentConfig(), text_provider="anthropic")
        set_runtime_config(cfg)
        clear_runtime_config()
        assert get_runtime_config() is None

    def test_get_agent_config_returns_override(self):
        """有运行时覆盖时，get_agent_config 返回覆盖配置。"""
        cfg = replace(AgentConfig(), text_provider="anthropic", text_model_name="claude-test")
        set_runtime_config(cfg)
        result = get_agent_config()
        assert result is cfg
        assert result.text_provider == "anthropic"
        assert result.text_model_name == "claude-test"

    def test_get_agent_config_falls_back_to_env(self):
        """无运行时覆盖时，get_agent_config 从环境变量构建。"""
        clear_runtime_config()
        result = get_agent_config()
        # 应该返回默认配置（或环境变量配置），不是覆盖
        assert isinstance(result, AgentConfig)

    def test_override_does_not_affect_env_build(self):
        """_build_config_from_env 不受运行时覆盖影响。"""
        cfg = replace(AgentConfig(), text_provider="anthropic")
        set_runtime_config(cfg)
        env_config = _build_config_from_env()
        assert env_config is not cfg
        # env_config 不受覆盖影响
        assert env_config.text_provider != "anthropic" or os.getenv("AGENT_TEXT_PROVIDER") == "anthropic"


class TestSwitchProviderPersistence:
    """测试 switch_provider 的持久化和回滚。"""

    def teardown_method(self):
        clear_runtime_config()

    def test_switch_persists_config(self, tmp_path):
        """switch_provider 成功后，get_agent_config() 返回新配置。"""
        from backend.app.config import Settings
        from backend.app.factory import create_app

        settings = Settings(data_dir=tmp_path)
        app = create_app(settings)
        client = TestClient(app)

        # 设置 API Key
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            # 切换到 deepseek
            resp = client.post(
                "/api/v1/agent/provider/switch",
                json={"provider": "deepseek"},
                headers=AUTH_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

            # 验证配置已持久化
            cfg = get_agent_config()
            assert cfg.text_provider == "deepseek"
            assert cfg.text_api_key_env == "DEEPSEEK_API_KEY"

    def test_switch_fail_rolls_back(self, tmp_path):
        """switch_provider 失败时，配置回滚到旧值。"""
        from backend.app.config import Settings
        from backend.app.factory import create_app

        settings = Settings(data_dir=tmp_path)
        app = create_app(settings)
        client = TestClient(app)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            # 先切换到 deepseek 成功
            resp = client.post(
                "/api/v1/agent/provider/switch",
                json={"provider": "deepseek"},
                headers=AUTH_HEADERS,
            )
            assert resp.json()["success"] is True
            old_cfg = get_agent_config()

            # 尝试切换到 anthropic，但 mock 创建编排器失败
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                with patch("backend.app.routers.agent.get_or_create_orchestrator", new_callable=AsyncMock) as mock_orch:
                    mock_orch.side_effect = RuntimeError("连接失败")
                    resp = client.post(
                        "/api/v1/agent/provider/switch",
                        json={"provider": "anthropic"},
                        headers=AUTH_HEADERS,
                    )
                    assert resp.json()["success"] is False

                # 验证配置已回滚
                cfg = get_agent_config()
                assert cfg.text_provider == old_cfg.text_provider

    def test_switch_no_api_key_returns_failure(self, tmp_path):
        """switch_provider 在 API Key 未配置时返回失败。"""
        from backend.app.config import Settings
        from backend.app.factory import create_app

        settings = Settings(data_dir=tmp_path)
        app = create_app(settings)
        client = TestClient(app)

        # 确保没有 API Key
        env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            resp = client.post(
                "/api/v1/agent/provider/switch",
                json={"provider": "anthropic"},
                headers=AUTH_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "API Key" in data["message"] or "api_key" in data["message"].lower()

    def test_provider_info_reflects_override(self, tmp_path):
        """get /provider 返回运行时覆盖的配置信息。"""
        from backend.app.config import Settings
        from backend.app.factory import create_app

        settings = Settings(data_dir=tmp_path)
        app = create_app(settings)
        client = TestClient(app)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            # 切换到 deepseek
            client.post(
                "/api/v1/agent/provider/switch",
                json={"provider": "deepseek", "model_name": "deepseek-reasoner"},
                headers=AUTH_HEADERS,
            )

            # 查询 provider info
            resp = client.get("/api/v1/agent/provider", headers=AUTH_HEADERS)
            assert resp.status_code == 200
            data = resp.json()
            assert data["provider"] == "deepseek"
            assert data["model_name"] == "deepseek-reasoner"
