"""智谱 GLM 专用适配器测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app


@pytest.mark.asyncio
async def test_zhipu_text_uses_v4_path_thinking_and_multimodal_shape():
    from backend.app.agent.providers.zhipu import ZhipuTextProvider

    provider = ZhipuTextProvider(
        api_key="sk-zhipu-test",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-5.3-flash",
        thinking_enabled=True,
        reasoning_effort="max",
    )
    captured: dict = {}

    async def fake_request(payload):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "[[1,2,3,4]]"}}]}

    provider._request_with_retry = fake_request
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
            {"type": "text", "text": "返回坐标"},
        ],
    }]
    response = await provider.complete(messages, model="glm-5.3-flash")

    assert response.content == "[[1,2,3,4]]"
    assert provider._chat_path == "/chat/completions"
    assert captured["payload"]["messages"] == messages
    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert captured["payload"]["reasoning_effort"] == "max"


def test_zhipu_is_available_as_a_saved_model_provider():
    from backend.app.agent.providers.zhipu import ZHIPU_DEFAULT_BASE_URL, ZHIPU_DEFAULT_MODEL
    from backend.app.routers.agent import _profile_endpoint

    assert ZHIPU_DEFAULT_MODEL == "glm-5.3-flash"
    assert ZHIPU_DEFAULT_BASE_URL == "https://open.bigmodel.cn/api/paas/v4"
    assert _profile_endpoint(
        ZHIPU_DEFAULT_BASE_URL, "zhipu"
    ) == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


@pytest.mark.asyncio
async def test_zhipu_glm53_omits_thinking_when_disabled():
    from backend.app.agent.providers.zhipu import ZhipuTextProvider

    provider = ZhipuTextProvider(
        api_key="sk-zhipu-test",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-5.3-flash",
        thinking_enabled=False,
    )
    captured: dict = {}

    async def fake_request(payload):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}]}

    provider._request_with_retry = fake_request
    await provider.complete([{"role": "user", "content": "hello"}])

    assert "thinking" not in captured["payload"]
    assert "reasoning_effort" not in captured["payload"]


def test_bigmodel_key_is_routed_away_from_zai_legacy_default():
    from backend.app.agent.providers.zhipu import (
        ZHIPU_BIGMODEL_BASE_URL,
        ZHIPU_ZAI_BASE_URL,
        ZhipuTextProvider,
        resolve_zhipu_base_url,
    )

    bigmodel_key = "0123456789abcdef0123456789abcdef.secret-part-123456"
    assert resolve_zhipu_base_url(ZHIPU_ZAI_BASE_URL, bigmodel_key) == ZHIPU_BIGMODEL_BASE_URL

    provider = ZhipuTextProvider(
        api_key=bigmodel_key,
        base_url=ZHIPU_ZAI_BASE_URL,
    )
    assert provider._base_url == ZHIPU_BIGMODEL_BASE_URL


def test_zai_key_keeps_explicit_zai_endpoint():
    from backend.app.agent.providers.zhipu import (
        ZHIPU_ZAI_BASE_URL,
        resolve_zhipu_base_url,
    )

    assert resolve_zhipu_base_url(ZHIPU_ZAI_BASE_URL, "zai-key-without-dot") == ZHIPU_ZAI_BASE_URL


@pytest.mark.asyncio
async def test_zhipu_glm4_flash_omits_unsupported_thinking_parameter():
    from backend.app.agent.providers.zhipu import ZhipuTextProvider

    provider = ZhipuTextProvider(
        api_key="sk-zhipu-test",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
        thinking_enabled=True,
    )
    captured: dict = {}

    async def fake_request(payload):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}]}

    provider._request_with_retry = fake_request
    await provider.complete([{"role": "user", "content": "hello"}])

    assert "thinking" not in captured["payload"]
    assert "reasoning_effort" not in captured["payload"]


def test_saved_api_key_is_preserved_when_model_is_reopened_and_resaved(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))
    app = create_app(Settings(data_dir=tmp_path))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        first = client.post("/api/v1/agent/models", headers=headers, json={
            "display_name": "智谱测试",
            "provider": "zhipu",
            "model_name": "glm-4-flash",
            "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "api_key": "sk-zhipu-persisted",
            "apply": False,
        })
        assert first.status_code == 200, first.text
        profile_id = first.json()["id"]
        assert first.json()["api_key_configured"] is True

        reopened = client.post("/api/v1/agent/models", headers=headers, json={
            "id": profile_id,
            "display_name": "智谱测试",
            "provider": "zhipu",
            "model_name": "glm-4-flash",
            "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "apply": False,
        })
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["api_key_configured"] is True


@pytest.mark.asyncio
async def test_profile_activation_builds_provider_with_selected_profile_key(tmp_path, monkeypatch):
    """切换档案时不能用上一个档案的 Key 创建并缓存 orchestrator。"""
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))

    from backend.app.agent.config import (
        AgentConfig,
        clear_runtime_config,
        get_agent_config,
        set_runtime_config,
    )
    from backend.app.agent.keyvault import save_api_key
    from backend.app.routers import agent as agent_router

    save_api_key("old-profile-key", "old-profile")
    save_api_key("selected-profile-key", "selected-profile")
    set_runtime_config(
        AgentConfig(
            text_provider="zhipu",
            text_model_profile_id="old-profile",
        ),
        persist=False,
    )
    captured = {}

    async def fake_close_orchestrator():
        return None

    async def fake_get_or_create_orchestrator(config=None):
        captured["profile_id"] = config.text_model_profile_id
        captured["api_key"] = config.text_api_key
        return object()

    monkeypatch.setattr(agent_router, "close_orchestrator", fake_close_orchestrator)
    monkeypatch.setattr(
        agent_router,
        "get_or_create_orchestrator",
        fake_get_or_create_orchestrator,
    )
    monkeypatch.setattr(
        "backend.app.agent.runtime.harness_runtime.is_harness_mode",
        lambda: False,
    )

    try:
        ok, message = await agent_router._activate_model_profile({
            "id": "selected-profile",
            "provider": "openai_compat",
            "base_url": "https://relay.example/v1",
            "model_name": "relay-model",
            "api_key_env": "UNSET_RELAY_KEY",
            "thinking_enabled": False,
            "supports_reasoning": False,
            "supports_tool_calls": True,
            "supports_vision": False,
        })
        assert ok is True, message
        assert captured == {
            "profile_id": "selected-profile",
            "api_key": "selected-profile-key",
        }
        assert get_agent_config().text_model_profile_id == "selected-profile"
    finally:
        clear_runtime_config(persist=False)
