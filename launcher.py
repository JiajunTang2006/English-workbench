#!/usr/bin/env python3
"""Start TeachMate Web with the local SQLite WorkBench data service."""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from backend.app.config import get_settings
from teachmate_runtime import (
    harness_headless_patch,
    harness_paths,
    resolve_ports,
    stop_process,
    teachmate_enabled,
    wait_for_http,
)


PROJECT_DIR = Path(__file__).resolve().parent
NETWORK_SETTINGS = get_settings()
HOST = NETWORK_SETTINGS.host
PORT = NETWORK_SETTINGS.port


def open_browser(url: str) -> bool:
    """Open the local WorkBench URL explicitly in Safari on macOS."""
    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["open", "-a", "Safari", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    return bool(webbrowser.open(url))


def find_python() -> Path:
    candidates = [
        PROJECT_DIR / ".venv" / "bin" / "python",
        PROJECT_DIR / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("找不到可用的 Python，请先创建 .venv 并按 README「首次安装」安装 backend/requirements.lock 与 vendored Harness SDK")


def port_in_use(port: int) -> bool:
    with socket.socket() as client:
        client.settimeout(0.2)
        return client.connect_ex((HOST, port)) == 0


def wait_until_ready(process: subprocess.Popen, port: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            # 使用原始回环 socket，不经过 urllib 的系统代理配置；启动探测
            # 只需要确认本机服务返回 200，不需要读取响应体。
            with socket.create_connection((HOST, port), timeout=0.5) as client:
                client.sendall(
                    f"GET /health HTTP/1.0\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode("ascii")
                )
                status_line = client.recv(128).split(b"\r\n", 1)[0]
            if b" 200 " in status_line:
                return True
            last_error = RuntimeError(status_line.decode("latin-1", errors="replace"))
        except (TimeoutError, OSError) as error:
            last_error = error
            time.sleep(0.15)
    # Uvicorn 已经开始监听时，健康探测偶发被系统代理、网络过滤器或
    # macOS 本地回环策略拦截；这不应把一个已经可访问的本地工作台判成启动失败。
    # 启动前已检查过端口占用，因此这里的监听端口只能来自当前子进程。
    if process.poll() is None and port_in_use(port):
        detail = f"（健康探测未返回 200：{last_error}）" if last_error else ""
        print(f"本地服务已监听 {HOST}:{port}，继续打开工作台{detail}。", file=sys.stderr)
        return True
    return False


def start_legacy_workbench() -> int:
    if port_in_use(PORT):
        print(f"端口 {PORT} 已被占用，请关闭旧工作台进程后重试。", file=sys.stderr)
        return 1
    python = find_python()
    token = secrets.token_urlsafe(32)
    environment = os.environ.copy()
    environment["WORKBENCH_TOKEN"] = token
    process = subprocess.Popen(
        [str(python), "-m", "backend.app"],
        cwd=PROJECT_DIR,
        env=environment,
    )
    if not wait_until_ready(process, PORT):
        process.terminate()
        process.wait(timeout=5)
        print("本地服务启动失败，请检查上方错误信息。", file=sys.stderr)
        return 1
    url = f"http://{HOST}:{PORT}/workbench?token={quote(token)}"
    print("English Workbench 已启动。")
    print("关闭此终端窗口将停止本地服务。")
    if not open_browser(url):
        print(f"无法自动打开浏览器，请手动访问：{url}", file=sys.stderr)
    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\n正在关闭工作台……")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


def start_teachmate_workbench() -> int:
    """Start WorkBench with TeachMate — FastAPI backend only, no Harness Web UI.

    The FastAPI backend serves the WorkBench/TeachMate frontend directly.
    Harness runs in headless mode as a hidden subprocess for agent inference.
    """
    _browser_port, backend_port = resolve_ports(PORT)
    # Use backend_port as the single serving port (no separate browser port).
    backend_port = PORT
    if port_in_use(backend_port):
        print(f"WorkBench 端口 {backend_port} 已被占用，请关闭旧工作台进程后重试。", file=sys.stderr)
        return 1

    python = find_python()
    token = secrets.token_urlsafe(32)
    backend_environment = os.environ.copy()
    backend_environment["WORKBENCH_TOKEN"] = token
    backend_environment["WORKBENCH_PORT"] = str(backend_port)
    # 源码运行包未携带 Harness 时自动使用 Python Agent，避免启动即失败。
    harness_ready = harness_paths()[0].is_file() and harness_headless_patch().is_file()
    backend_environment.setdefault("AGENT_RUNTIME", "harness" if harness_ready else "legacy")
    backend = subprocess.Popen(
        [str(python), "-m", "backend.app"],
        cwd=PROJECT_DIR,
        env=backend_environment,
    )
    try:
        # 四路 Harness 并行启动时需要额外的 Node 初始化时间；启动脚本
        # 不能在后端仍处于正常初始化阶段就误判失败。
        startup_timeout = 90 if harness_ready else 30
        print("正在启动 TeachMate，请稍候……")
        if not wait_until_ready(backend, backend_port, timeout=startup_timeout):
            print("WorkBench 数据服务启动失败，请检查上方错误信息。", file=sys.stderr)
            return 1

        url = f"http://{HOST}:{backend_port}/workbench?token={quote(token)}"
        print("English Workbench 已启动。")
        print("关闭此终端窗口将停止本地服务。")
        if not open_browser(url):
            print(f"无法自动打开浏览器，请手动访问：{url}", file=sys.stderr)
        return backend.wait()
    except KeyboardInterrupt:
        print("\n正在关闭 TeachMate……")
        return 0
    finally:
        stop_process(backend)


def start_workbench() -> int:
    if not teachmate_enabled():
        return start_legacy_workbench()
    return start_teachmate_workbench()


def main() -> int:
    return start_workbench()


if __name__ == "__main__":
    raise SystemExit(main())
