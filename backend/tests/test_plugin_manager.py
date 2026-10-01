"""Plugin Manager v1：manifest、安装、启停、健康检查和回滚边界。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.services.plugin_manager import PluginManager, PluginValidationError
from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app


def _write_plugin(root: Path, plugin_id: str = "sample_plugin") -> Path:
    manifest = root / ".teachmate-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "id": plugin_id,
        "display_name": "Sample Plugin",
        "description": "测试插件",
        "version": "0.1.0",
        "api_version": "teachmate-plugin/v1",
        "runtime": {"transport": "internal"},
        "permissions": ["teaching.read"],
        "capabilities": [{"id": "sample", "name": "测试能力"}],
    }), encoding="utf-8")
    return root


def _zip_plugin(tmp_path: Path) -> Path:
    source = _write_plugin(tmp_path / "sample_plugin")
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in source.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(source))
    return archive


def test_bundled_manifests_are_discovered(tmp_path):
    manager = PluginManager(tmp_path, Path(__file__).parents[2] / "plugins" / "bundled")
    plugins = manager.list_plugins()
    assert {item["id"] for item in plugins} >= {"exam_analysis", "student_diagnosis", "review_plan", "targeted_practice"}
    assert all(item["source"] == "bundled" for item in plugins)


def test_install_toggle_health_and_remove(tmp_path):
    manager = PluginManager(tmp_path, tmp_path / "empty-bundled")
    plugin = manager.install_archive(_zip_plugin(tmp_path))
    assert plugin["id"] == "sample_plugin"
    assert plugin["enabled"] is True
    assert manager.health_check("sample_plugin")["health"] == "ready"
    assert manager.set_enabled("sample_plugin", False)["enabled"] is False
    assert manager.health_check("sample_plugin")["health"] == "disabled"
    manager.remove("sample_plugin")
    assert manager.get_plugin("sample_plugin") is None


def test_zip_path_traversal_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "bad")
    manager = PluginManager(tmp_path, tmp_path / "empty-bundled")
    with pytest.raises(PluginValidationError, match="越界路径"):
        manager.install_archive(archive)


def test_manifest_unknown_permission_rejected(tmp_path):
    manager = PluginManager(tmp_path, tmp_path / "empty-bundled")
    with pytest.raises(PluginValidationError, match="未知权限"):
        manager.validate_manifest({"id": "bad_plugin", "version": "1", "permissions": ["filesystem.root"]})


def test_stdio_plugin_health_and_tool_call(tmp_path):
    source = _write_plugin(tmp_path / "stdio_plugin", "stdio_plugin")
    manifest = json.loads((source / ".teachmate-plugin" / "plugin.json").read_text(encoding="utf-8"))
    manifest["runtime"] = {"transport": "stdio", "command": "python3", "args": ["server.py"]}
    manifest["capabilities"] = [{"id": "echo", "name": "回显"}]
    (source / ".teachmate-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "server.py").write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " m=json.loads(line); i=m.get('id')\n"
        " if m.get('method') == 'initialize': print(json.dumps({'jsonrpc':'2.0','id':i,'result':{}}), flush=True)\n"
        " elif m.get('method') == 'tools/list': print(json.dumps({'jsonrpc':'2.0','id':i,'result':{'tools':[{'name':'echo'}]}}), flush=True)\n"
        " elif m.get('method') == 'tools/call': print(json.dumps({'jsonrpc':'2.0','id':i,'result':{'content':[{'type':'text','text':'ok'}]}}), flush=True)\n",
        encoding="utf-8",
    )
    manager = PluginManager(tmp_path, tmp_path / "empty-bundled")
    archive = tmp_path / "stdio.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in source.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(source))
    manager.install_archive(archive)
    assert manager.health_check("stdio_plugin")["health"] == "ready"
    assert manager.call_tool("stdio_plugin", "echo", {"value": 1})["content"][0]["text"] == "ok"


def test_http_plugin_health_and_tool_call(tmp_path, monkeypatch):
    source = _write_plugin(tmp_path / "http_plugin", "http_plugin")
    manifest = json.loads((source / ".teachmate-plugin" / "plugin.json").read_text(encoding="utf-8"))
    manifest["runtime"] = {"transport": "http", "url": "http://127.0.0.1:9900/mcp"}
    manifest["capabilities"] = [{"id": "echo", "name": "回显"}]
    (source / ".teachmate-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive = tmp_path / "http.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in source.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(source))

    class _Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, timeout, **_kwargs):
        assert request.full_url == "http://127.0.0.1:9900/mcp"
        body = json.loads(request.data.decode("utf-8"))
        if "id" not in body:
            return _Response({})
        if body["method"] == "tools/list":
            return _Response({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "echo"}]}})
        if body["method"] == "tools/call":
            return _Response({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "ok"}]}})
        return _Response({"jsonrpc": "2.0", "id": body["id"], "result": {}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    manager = PluginManager(tmp_path, tmp_path / "empty-bundled")
    manager.install_archive(archive)
    assert manager.health_check("http_plugin")["health"] == "ready"
    assert manager.call_tool("http_plugin", "echo")["content"][0]["text"] == "ok"


def test_http_plugin_sends_bearer_token_and_mcp_session(tmp_path, monkeypatch):
    source = _write_plugin(tmp_path / "auth_http_plugin", "auth_http_plugin")
    manifest = json.loads((source / ".teachmate-plugin" / "plugin.json").read_text(encoding="utf-8"))
    manifest["runtime"] = {
        "transport": "http", "url": "http://127.0.0.1:9901/mcp",
        "bearer_token_env_var": "TEST_MCP_TOKEN", "require_bearer_token": True,
    }
    manifest["capabilities"] = [{"id": "echo", "name": "回显"}]
    (source / ".teachmate-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive = tmp_path / "auth_http.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in source.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(source))

    class _Response:
        def __init__(self, payload, headers=None):
            self.payload = json.dumps(payload).encode("utf-8")
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

        def getheader(self, name):
            return self.headers.get(name)

    seen = []

    def fake_urlopen(request, timeout, **_kwargs):
        seen.append(dict(request.header_items()))
        body = json.loads(request.data.decode("utf-8"))
        if "id" not in body:
            return _Response({})
        if body["method"] == "initialize":
            return _Response({"jsonrpc": "2.0", "id": body["id"], "result": {}}, {"Mcp-Session-Id": "s-1", "Content-Type": "application/json"})
        assert request.get_header("Mcp-session-id") == "s-1"
        return _Response({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "ok"}]}}, {"Content-Type": "application/json"})

    monkeypatch.setenv("TEST_MCP_TOKEN", "secret-token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    manager = PluginManager(tmp_path, tmp_path / "empty-bundled")
    manager.install_archive(archive)
    assert manager.call_tool("auth_http_plugin", "echo")["content"][0]["text"] == "ok"
    assert seen[0]["Authorization"] == "Bearer secret-token"
    assert seen[1]["Mcp-session-id"] == "s-1"


def test_plugin_manager_api_exposes_bundled_plugins(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CODEX_PLUGIN_ENABLED", "false")
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/v1/plugins", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["api_version"] == "teachmate-plugin/v1"
        # MONI 是后台只读数据源，不作为前端可管理插件返回；宿主仍保留其发现与调用能力。
        assert {item["id"] for item in body["plugins"]} == {"exam_analysis", "student_diagnosis", "review_plan", "targeted_practice"}
        assert app.state.plugin_manager.get_plugin("moni") is not None
        disabled = client.post("/api/v1/plugins/exam_analysis/disable", headers={"Authorization": f"Bearer {TOKEN}"})
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
