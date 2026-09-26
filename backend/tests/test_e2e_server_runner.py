from __future__ import annotations

from tools.run_e2e_server import server_started_successfully


def test_server_started_successfully_uses_supported_uvicorn_state():
    class StartedServer:
        started = True

    class FailedServer:
        started = False

    assert server_started_successfully(StartedServer()) is True
    assert server_started_successfully(FailedServer()) is False


def test_server_started_successfully_handles_older_or_partial_server_objects():
    assert server_started_successfully(object()) is False
