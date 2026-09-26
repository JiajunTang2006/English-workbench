import urllib.request
import sys

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.auth import TOKEN
from backend.app.config import Settings, get_settings
from backend.app.factory import create_app
from backend.app.version import APP_VERSION, SCHEMA_REVISION
from windows_launcher import create_server, ensure_standard_streams, wait_until_ready


def test_network_settings_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKBENCH_HOST", "127.0.0.2")
    monkeypatch.setenv("WORKBENCH_PORT", "9876")
    settings = get_settings()
    assert settings.host == "127.0.0.2"
    assert settings.port == 9876


def test_runtime_uses_central_version_constants(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    response = client.get(
        "/api/v1/runtime",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["version"] == APP_VERSION
    assert response.json()["schema"] == SCHEMA_REVISION


def test_browser_session_tracks_connections_and_disconnects(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    client = TestClient(app)
    before = app.state.browser_session.snapshot()
    assert before.connections == 0
    assert before.ever_connected is False

    with client.websocket_connect(f"/api/v1/runtime/session?token={TOKEN}") as websocket:
        websocket.send_text("ready")
        connected = app.state.browser_session.snapshot()
        assert connected.connections == 1
        assert connected.ever_connected is True

    disconnected = app.state.browser_session.snapshot()
    assert disconnected.connections == 0
    assert app.state.browser_session.should_stop(
        grace_seconds=8,
        now=disconnected.last_disconnected_at + 9,
    )


def test_browser_session_rejects_an_old_token(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    try:
        with client.websocket_connect("/api/v1/runtime/session?token=old-token"):
            assert False, "旧令牌连接应在 accept 前被拒绝"
    except WebSocketDisconnect as error:
        assert error.code == 4401


def test_ready_check_uses_the_current_launch_token(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert wait_until_ready("127.0.0.1", 8765, "current-token", timeout=0.1)
    assert captured == {
        "url": "http://127.0.0.1:8765/api/v1/runtime",
        "authorization": "Bearer current-token",
        "timeout": 0.5,
    }


def test_windowed_launcher_works_without_console_streams(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    ensure_standard_streams()
    app = create_app(Settings(data_dir=tmp_path))
    server = create_server(app, "127.0.0.1", 8765)

    assert sys.stdout is not None
    assert sys.stderr is not None
    assert server.config.log_config is None
    assert server.config.access_log is False
