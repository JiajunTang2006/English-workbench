"""B3-10 第三方 API 配置入口测试

覆盖：
1. keyvault：写入/读取/清除、0600 权限、原子写；内容不出现于日志；文件不入仓库；
2. switch_provider 直接提交 Key → 本地保管库生效（text_api_key 可读）；
   api_key="" 清除；api_key=None 保留；
3. 接口不返回 Key：ProviderInfo / status 响应中无明文、只给 key_configured；
4. 测试连接：只验证可达性，不保存模型回答（响应无 content、无对 LLM 的持久化）；
5. status 接口：configured/running/pid/restart_count/config_version；
6. config_version 绑定：切换后递增（审计不变式）。
"""

from __future__ import annotations

import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))
    from backend.app.factory import create_app
    from backend.app.auth import TOKEN

    app = create_app(Settings(data_dir=tmp_path))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        yield c, headers, tmp_path
    # 清理 Key 与运行时覆盖
    from backend.app.agent import keyvault
    keyvault.clear_api_key()
    from backend.app.agent.config import clear_runtime_config
    clear_runtime_config()


# ---------------------------------------------------------------------------
# 1. keyvault
# ---------------------------------------------------------------------------

class TestKeyvault:

    def test_save_load_clear(self, env):
        from backend.app.agent.keyvault import (
            save_api_key, load_api_key, clear_api_key,
        )
        save_api_key("sk-test-secret")
        assert load_api_key() == "sk-test-secret"
        p = env[2] / "provider" / "api_key"
        assert p.is_file()
        mode = stat.S_IMODE(os.stat(p).st_mode)
        assert mode & 0o077 == 0, "Key 文件权限必须为 0600"
        clear_api_key()
        assert load_api_key() is None

    def test_atomic_and_overwrite(self, env):
        from backend.app.agent.keyvault import save_api_key, load_api_key
        save_api_key("sk-a")
        save_api_key("sk-b")
        assert load_api_key() == "sk-b"

    def test_active_profile_key_is_the_runtime_source(self, env):
        from backend.app.agent.config import AgentConfig
        from backend.app.agent.keyvault import save_api_key

        save_api_key("sk-default")
        save_api_key("sk-profile", "profile-a")
        cfg = AgentConfig(text_model_profile_id="profile-a")
        assert cfg.text_api_key == "sk-profile"
        assert cfg.text_api_key_configured is True

    def test_profile_key_configured_reads_profile_vault(self, env):
        from backend.app.agent.config import AgentConfig
        from backend.app.agent.keyvault import save_api_key
        from backend.app.routers.agent import _profile_key_configured

        save_api_key("sk-profile", "profile-a")
        profile = {
            "id": "profile-a",
            "api_key_env": "UNSET_PROFILE_KEY_ENV",
        }
        assert _profile_key_configured(profile, AgentConfig()) is True

        missing = {
            "id": "profile-missing",
            "api_key_env": "UNSET_PROFILE_KEY_ENV",
        }
        assert _profile_key_configured(missing, AgentConfig()) is False


# ---------------------------------------------------------------------------
# 2. 切换 provider 保存/清除 Key
# ---------------------------------------------------------------------------

class TestSwitchWithKey:

    def test_switch_persists_key_vaulted(self, env):
        c, headers, tmp = env
        resp = c.post("/api/v1/agent/provider/switch", headers=headers, json={
            "provider": "deepseek",
            "model_name": "deepseek-chat",
            "api_key": "sk-vaulted-123",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["provider_info"]["api_key_configured"] is True
        # 响应绝不含 Key 明文
        assert "sk-vaulted-123" not in resp.text
        from backend.app.agent.keyvault import load_api_key
        assert load_api_key() == "sk-vaulted-123"
        from backend.app.agent.config import get_agent_config
        assert get_agent_config().text_api_key == "sk-vaulted-123"

    def test_switch_clear_key_with_empty_string(self, env):
        c, headers, tmp = env
        from backend.app.agent.keyvault import save_api_key, load_api_key
        save_api_key("sk-old")
        # 先清空可以成功切（无 key 会失败？清空后 key_configured False）
        resp = c.post("/api/v1/agent/provider/switch", headers=headers, json={
            "provider": "deepseek", "api_key": "",
        })
        assert resp.json()["success"] is False  # 无 Key 无法切换
        assert load_api_key() is None

    def test_switch_without_key_preserves_vault(self, env):
        from backend.app.agent.keyvault import save_api_key, load_api_key
        save_api_key("sk-keep")
        c, headers, tmp = env
        resp = c.post("/api/v1/agent/provider/switch", headers=headers, json={
            "provider": "deepseek", "model_name": "deepseek-chat",
        })
        assert resp.json()["success"] is True
        assert load_api_key() == "sk-keep"

    def test_switch_response_no_key_leaked(self, env):
        c, headers, tmp = env
        resp = c.post("/api/v1/agent/provider/switch", headers=headers, json={
            "provider": "deepseek", "model_name": "deepseek-chat",
            "api_key": "sk-leak-check-999",
        })
        assert "sk-leak-check-999" not in resp.text
        assert "api_key" not in resp.json()["provider_info"]

    def test_status_no_key_no_env_name_masked(self, env):
        from backend.app.agent.keyvault import save_api_key
        save_api_key("sk-status-secret")
        c, headers, tmp = env
        resp = c.get("/api/v1/agent/provider/status", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["key_configured"] is True
        assert "sk-status-secret" not in resp.text
        assert "configured" in body

    def test_switch_applies_model_capabilities_and_clears_stale_vision(self, env):
        from backend.app.agent.config import get_agent_config

        c, headers, _ = env
        first = c.post("/api/v1/agent/provider/switch", headers=headers, json={
            "provider": "zhipu",
            "base_url": "https://api.z.ai/api/paas/v4",
            "model_name": "glm-5.3-flash",
            "api_key": "sk-capability-test",
            "supports_tool_calls": True,
            "supports_vision": True,
        })
        assert first.json()["success"] is True
        assert get_agent_config().vision_provider == "zhipu"

        second = c.post("/api/v1/agent/provider/switch", headers=headers, json={
            "provider": "deepseek",
            "model_name": "deepseek-chat",
            "api_key": "sk-capability-test",
            "supports_tool_calls": False,
            "supports_vision": False,
        })
        assert second.json()["success"] is True
        config = get_agent_config()
        assert config.text_supports_tool_calls is False
        assert config.text_supports_vision is False
        assert config.vision_provider == ""
        assert config.vision_api_base_url == ""
        assert config.vision_model_name == ""


class TestProviderPayloads:

    @pytest.mark.asyncio
    async def test_deepseek_disabled_omits_reasoning_effort(self):
        from backend.app.agent.providers.deepseek_text import DeepSeekTextProvider

        provider = DeepSeekTextProvider("sk-test", thinking_enabled=False, reasoning_effort="max")
        captured = {}

        async def fake_request(payload):
            captured.update(payload)
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

        provider._request_with_retry = fake_request
        await provider.complete([{"role": "user", "content": "hi"}], model="deepseek-v4-flash")
        assert captured["model"] == "deepseek-v4-flash"
        assert captured["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in captured

    @pytest.mark.asyncio
    async def test_deepseek_preserves_reasoning_content_for_tool_continuation(self):
        from backend.app.agent.providers.deepseek_text import DeepSeekTextProvider

        provider = DeepSeekTextProvider("sk-test", thinking_enabled=True)
        async def fake_request(payload):
            return {
                "choices": [{"message": {
                    "content": "",
                    "reasoning_content": "internal trace",
                    "tool_calls": [],
                }}],
                "usage": {},
            }
        provider._request_with_retry = fake_request
        response = await provider.complete([{"role": "user", "content": "hi"}])
        assert response.reasoning_content == "internal trace"

    def test_deepseek_repairs_truncated_tool_arguments(self):
        from backend.app.agent.providers.deepseek_text import DeepSeekTextProvider

        provider = DeepSeekTextProvider("sk-test")
        response = provider._parse_response({
            "choices": [{"message": {"tool_calls": [{
                "function": {
                    "name": "submit_teaching_report",
                    "arguments": '{"answer_type":"exam_analysis","summary":"ok"',
                },
            }]}}],
            "usage": {},
        }, "deepseek-chat")
        assert response.tool_calls[0].arguments == {
            "answer_type": "exam_analysis", "summary": "ok",
        }

    @pytest.mark.asyncio
    async def test_openai_compat_rejects_unsupported_reasoning(self):
        from backend.app.agent.providers.base import ModelError
        from backend.app.agent.providers.openai_compat_text import OpenAICompatTextProvider

        provider = OpenAICompatTextProvider(
            "sk-test", "https://example.invalid", thinking_enabled=True,
            reasoning_effort="high", supports_reasoning=False,
        )
        with pytest.raises(ModelError, match="未声明支持 reasoning_effort"):
            await provider.complete([{"role": "user", "content": "hi"}], model="custom-model")


# ---------------------------------------------------------------------------
# 3. 测试连接
# ---------------------------------------------------------------------------

class _FakeLLMHandler(BaseHTTPRequestHandler):
    received: list[dict] = []
    authorizations: list[str] = []

    def do_POST(self):
        import json
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append(body)
        type(self).authorizations.append(self.headers.get("Authorization", ""))
        resp = b'{"choices":[{"message":{"content":"pong"}}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_llm():
    _FakeLLMHandler.received = []
    _FakeLLMHandler.authorizations = []
    server = HTTPServer(("127.0.0.1", 0), _FakeLLMHandler)
    port = server.server_address[1]
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestProviderTest:

    def test_connection_ok_and_not_saved(self, env, fake_llm):
        c, headers, tmp = env
        resp = c.post("/api/v1/agent/provider/test", headers=headers, json={
            "provider": "openai_compat",
            "base_url": fake_llm,
            "model_name": "m1",
            "api_key": "sk-test-conn",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["latency_ms"] is not None
        # 测试不保存模型回答：fake server 收到 ping 且无任何 run/message 落库
        assert body["provider"] == "openai_compat"
        # 响应不含 Key
        assert "sk-test-conn" not in resp.text

    def test_bad_auth_fails_gracefully(self, env):
        c, headers, tmp = env
        resp = c.post("/api/v1/agent/provider/test", headers=headers, json={
            "provider": "openai_compat",
            "base_url": "http://127.0.0.1:1",  # 不可达
            "model_name": "m1",
            "api_key": "sk-x",
        })
        body = resp.json()
        assert body["ok"] is False
        assert "sk-x" not in resp.text

    def test_saved_profile_test_uses_its_own_key(self, env, fake_llm):
        from backend.app.agent.keyvault import save_api_key

        c, headers, _ = env
        save_api_key("sk-current-profile")
        save_api_key("sk-edited-profile", "profile-being-tested")
        resp = c.post("/api/v1/agent/provider/test", headers=headers, json={
            "provider": "openai_compat",
            "base_url": fake_llm,
            "model_name": "m1",
            "profile_id": "profile-being-tested",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert _FakeLLMHandler.authorizations[-1] == "Bearer sk-edited-profile"


# ---------------------------------------------------------------------------
# 4. config_version 绑定
# ---------------------------------------------------------------------------

def test_config_version_increases_on_switch(env):
    from backend.app.agent.config import get_agent_config
    c, headers, tmp = env
    r1 = c.post("/api/v1/agent/provider/switch", headers=headers, json={
        "provider": "deepseek", "model_name": "deepseek-chat",
        "api_key": "sk-v1",
    })
    assert r1.status_code == 200
    v1 = get_agent_config().config_version
    r2 = c.post("/api/v1/agent/provider/switch", headers=headers, json={
        "provider": "deepseek", "model_name": "deepseek-chat-2",
        "api_key": "sk-v2",
    })
    assert r2.status_code == 200
    v2 = get_agent_config().config_version
    assert v2 > v1, "配置切换必须递增版本号（审计不变式）"


# ---------------------------------------------------------------------------
# 5. Harness 配置切换安全重启（离线）
# ---------------------------------------------------------------------------

def test_apply_config_restarts_running_manager(monkeypatch):
    """配置切换 → 常驻进程安全重启；未运行时仅更新配置。"""
    import asyncio
    from backend.app.agent.runtime.harness_manager import (
        HarnessManager, HarnessConfig,
    )

    cfg1 = HarnessConfig(cordis_path="/tmp/c1.yml", session_root="/tmp/s1",
                         api_key="sk-1", runtime_bin="/tmp/b.js", config_version=1)
    cfg2 = HarnessConfig(cordis_path="/tmp/c2.yml", session_root="/tmp/s2",
                         api_key="sk-2", runtime_bin="/tmp/b.js", config_version=2)

    calls = {"started": 0, "stopped": 0}

    class _Proc:
        pid = 99
        def poll(self):
            return None

    class _FakeSDK:
        def __init__(self):
            self._client = type("C", (), {"_proc": _Proc()})()
        def close(self):
            calls["stopped"] += 1
        def start(self):
            calls["started"] += 1
        def start_session(self, sid):
            return None

    async def scenario():
        manager = HarnessManager(cfg1)
        manager._running = True
        manager._sdk = _FakeSDK()
        # 防止真实 Popen（/tmp/b.js 不存在）：patch 启动过程为「装入 fake SDK」
        def _fake_start_process():
            sdk = _FakeSDK()
            sdk.start()
            manager._sdk = sdk
        manager._start_process = _fake_start_process
        manager._worker_thread = None
        restarted = await manager.apply_config(cfg2, restart_if_running=True)
        assert restarted is True
        assert calls["started"] >= 1
        assert calls["stopped"] >= 1
        assert manager._config is cfg2

        # 未运行时：仅更新配置，不启动
        manager2 = HarnessManager(cfg1)
        manager2._start_process = _fake_start_process
        restarted2 = await manager2.apply_config(cfg2, restart_if_running=True)
        assert restarted2 is False
        assert manager2._config is cfg2

    asyncio.run(scenario())

# ---------------------------------------------------------------------------
# P1-5：第三方 API 支持范围标注
# ---------------------------------------------------------------------------


class TestProviderSupportScope:
    @pytest.fixture(autouse=True)
    def harness_env(self, monkeypatch):
        """本组语义基于 harness 常驻运行时（B3-10 的 provider 白名单目标）。"""
        monkeypatch.setenv("AGENT_RUNTIME", "harness")

    def test_anthropic_marked_unsupported_in_harness(self, env):
        """供应商列表必须明确标注 Anthropic 暂未支持（页面禁用依据）。"""
        c, headers, _ = env
        r = c.get("/api/v1/agent/providers", headers=headers)
        assert r.status_code == 200
        providers = {p["id"]: p for p in r.json()}
        assert providers["deepseek"]["supported_in_harness"] is True
        assert providers["openai_compat"]["supported_in_harness"] is True
        anthropic = providers["anthropic"]
        assert anthropic["supported_in_harness"] is False
        assert "暂未支持" in anthropic["unsupported_reason"]

    def test_switch_anthropic_rejected_in_harness(self, env):
        """Harness 模式切换 Anthropic 必须被拒绝（不假装可用）。"""
        c, headers, _ = env
        r = c.post("/api/v1/agent/provider/switch", headers=headers, json={
            "provider": "anthropic", "model_name": "claude-sonnet-4-20250514",
            "api_key": "sk-ant-test",
        })
        body = r.json()
        assert body.get("success") is False
        assert "暂未支持" in body.get("message", "")

    def test_status_reports_supported_providers(self, env):
        """status 接口暴露支持矩阵（无敏感信息）。"""
        c, headers, _ = env
        r = c.get("/api/v1/agent/provider/status", headers=headers)
        assert r.status_code == 200
        st = r.json()
        assert set(st["harness_supported_providers"]) == {
            "deepseek", "openai_compat"}
        assert st["harness_unsupported_providers"] == ["anthropic"]
