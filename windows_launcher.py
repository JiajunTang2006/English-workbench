"""Windows 打包入口：启动 TeachMate Web 与本地 WorkBench 数据服务。"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

import uvicorn

from desktop_shell import acquire_single_instance, activate_existing_instance, open_desktop_window
from teachmate_runtime import harness_sdk_ready, teachmate_enabled

SESSION_CONNECT_TIMEOUT = 45
SESSION_DISCONNECT_GRACE = 8


def ensure_standard_streams() -> None:
    """Provide streams for GUI executables built without a console window."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def create_server(app, host: str, port: int) -> uvicorn.Server:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    return uvicorn.Server(config)


def wait_until_ready(host: str, port: int, token: str, timeout: float = 15, thread=None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if thread is not None and not thread.is_alive():
            return False
        try:
            request = urllib.request.Request(
                f"http://{host}:{port}/api/v1/runtime",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.15)
    return False


def monitor_browser_session(app, server, thread) -> None:
    connect_deadline = time.monotonic() + SESSION_CONNECT_TIMEOUT
    while thread.is_alive():
        snapshot = app.state.browser_session.snapshot()
        if app.state.browser_session.should_stop(grace_seconds=SESSION_DISCONNECT_GRACE):
            print("工作台网页已关闭，正在停止本地服务……")
            server.should_exit = True
            break
        if not snapshot.ever_connected and time.monotonic() >= connect_deadline:
            print("未检测到工作台网页连接，本地服务已自动停止。")
            server.should_exit = True
            break
        thread.join(timeout=0.5)


def main_legacy() -> int:
    ensure_standard_streams()
    token = os.getenv("WORKBENCH_TOKEN") or secrets.token_urlsafe(32)
    os.environ["WORKBENCH_TOKEN"] = token
    os.environ.setdefault("MONI_AUTO_SYNC", "1")

    from backend.app.config import get_settings
    from backend.app.factory import create_app

    settings = get_settings()
    instance_lock = acquire_single_instance(settings.data_dir)
    if instance_lock is None:
        if activate_existing_instance(settings.data_dir):
            print("English WorkBench 已在运行，已唤醒现有窗口。")
        else:
            print("English WorkBench 已在运行，请从菜单栏或系统托盘打开现有窗口。")
        return 0
    app = create_app(settings)
    server = create_server(app, settings.host, settings.port)
    # Uvicorn 运行在后台线程，信号由主线程处理。
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, name="workbench-server", daemon=True)
    thread.start()
    if not wait_until_ready(settings.host, settings.port, token, thread=thread):
        server.should_exit = True
        thread.join(timeout=5)
        print("本地服务启动失败。可能已有工作台正在运行，请关闭原网页并稍候重试。")
        return 1

    url = f"http://{settings.host}:{settings.port}/workbench?token={quote(token)}"
    print("English Workbench 已启动。")
    embedded = open_desktop_window(url, title="English WorkBench", data_dir=settings.data_dir)

    try:
        if embedded:
            server.should_exit = True
        else:
            print("已回退到浏览器模式，关闭所有工作台网页后本地服务将在约8秒内自动停止。")
            monitor_browser_session(app, server, thread)
        thread.join(timeout=5)
    except KeyboardInterrupt:
        print("正在关闭工作台……")
        server.should_exit = True
        thread.join(timeout=5)
    return 0


def main_teachmate() -> int:
    """单端口自研界面：FastAPI 直接服务 WorkBench/TeachMate 前端。

    不启动 Harness Web UI（dsh web）——产品不使用 DeepSeek 自带前端，
    Harness 仅以 headless 引擎形式由后端 lifespan 在 AGENT_RUNTIME=harness
    下自管理（B2-01），前端固定打开自研的 WorkBench 页面。
    """
    ensure_standard_streams()
    token = os.getenv("WORKBENCH_TOKEN") or secrets.token_urlsafe(32)
    os.environ["WORKBENCH_TOKEN"] = token
    os.environ.setdefault("MONI_AUTO_SYNC", "1")
    # 完整桌面包内有 Harness 时启用常驻引擎；精简源码运行包没有 Node/Harness，
    # 自动回退到 Python Agent，保证解压后的代码可以直接启动。
    harness_ready = harness_sdk_ready()
    os.environ.setdefault("AGENT_RUNTIME", "harness" if harness_ready else "legacy")

    from backend.app.config import get_settings
    from backend.app.factory import create_app

    settings = get_settings()
    instance_lock = acquire_single_instance(settings.data_dir)
    if instance_lock is None:
        if activate_existing_instance(settings.data_dir):
            print("English WorkBench 已在运行，已唤醒现有窗口。")
        else:
            print("English WorkBench 已在运行，请从菜单栏或系统托盘打开现有窗口。")
        return 0
    app = create_app(settings)
    server = create_server(app, settings.host, settings.port)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, name="workbench-server", daemon=True)
    thread.start()
    if not wait_until_ready(settings.host, settings.port, token, thread=thread):
        server.should_exit = True
        thread.join(timeout=5)
        print("WorkBench 数据服务启动失败。可能已有工作台正在运行，请关闭后重试。")
        return 1

    url = f"http://{settings.host}:{settings.port}/workbench?token={quote(token)}"
    print("English Workbench 已启动。")
    embedded = open_desktop_window(url, title="English WorkBench", data_dir=settings.data_dir)

    try:
        if embedded:
            server.should_exit = True
        else:
            print("已回退到浏览器模式，关闭所有工作台网页后本地服务将在约8秒内自动停止。")
            monitor_browser_session(app, server, thread)
        thread.join(timeout=5)
    except KeyboardInterrupt:
        print("正在关闭 TeachMate……")
        server.should_exit = True
        thread.join(timeout=5)
    return 0


def main() -> int:
    return main_teachmate() if teachmate_enabled() else main_legacy()


if __name__ == "__main__":
    raise SystemExit(main())
