"""Shared launcher helpers for the Harness-based TeachMate frontend."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = PROJECT_DIR / "vendor" / "deepseek-harness-upstream"


def _resolve_packaged_runtime_root() -> Path:
    """Find the teachmate-runtime directory inside a PyInstaller bundle.

    On Windows/Linux onedir, ``_MEIPASS`` points to the bundle root where
    ``--add-data`` files are placed.  On macOS ``--windowed`` .app bundles,
    ``_MEIPASS`` is ``Contents/Frameworks/`` while the data files land in
    ``Contents/Resources/``.  Check both locations for robustness.

    注意（08-21 打包修复）：macOS 上 ``Contents/Frameworks/teachmate-runtime``
    是 PyInstaller 的原始 add-data 落点，其中 pnpm symlink 会被丢弃（@deepseek-ai
    闭包为空、不可用）；build_macos_app.sh 会用 fix_runtime_symlinks.py 在
    ``Contents/Resources/teachmate-runtime`` 重建 symlink。因此 **Resources 优先**，
    否则运行时拿到残缺闭包会报 jsonrpc-demo 入口缺失。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        meipass_path = Path(meipass)
        # macOS .app bundle: data files live in Contents/Resources/
        # （fix_runtime_symlinks 修复过的完整副本，优先）
        resources = meipass_path.parent / "Resources" / "teachmate-runtime"
        if resources.is_dir():
            return resources
        # Primary location (Windows/Linux, or macOS if PyInstaller changes)
        primary = meipass_path / "teachmate-runtime"
        if primary.is_dir():
            return primary
    return PROJECT_DIR / "teachmate-runtime"


PACKAGED_RUNTIME_ROOT = _resolve_packaged_runtime_root()


def harness_root() -> Path:
    """Return the bundled Harness tree when running from a packaged app."""
    # ``teachmate-runtime/`` is also present in a source checkout as a staging
    # directory.  It may contain a deploy snapshot whose pnpm symlinks were
    # stripped (notably ``eventsource-parser``/``zod``), so selecting it in
    # development makes the JSON-RPC child exit during initialize.  Only use
    # that tree for a real frozen bundle; source runs use the complete vendor
    # checkout below.
    if getattr(sys, "_MEIPASS", None) is not None:
        packaged_root = PACKAGED_RUNTIME_ROOT / "harness"
        if packaged_root.is_dir():
            return packaged_root
    return HARNESS_ROOT


def harness_paths() -> tuple[Path, Path, Path]:
    root = harness_root()
    return (
        root / "apps" / "cli" / "lib" / "bin.js",
        root / "examples" / "teachmate" / "cordis.yml",
        root / "apps" / "web" / "dist" / "index.html",
    )


def harness_headless_patch() -> Path:
    """Return the cordis overlay that combines headless mode + teachmate tools."""
    root = harness_root()
    return root / "examples" / "teachmate" / "cordis.headless.yml"


def harness_sdk_ready() -> bool:
    """Check the JSON-RPC runtime actually used by the current desktop app."""
    from backend.app.agent.runtime.harness_runtime import locate_runtime_launch
    runtime_bin, launch_args, _ = locate_runtime_launch()
    if launch_args:
        return True
    if runtime_bin:
        try:
            find_node()
            return True
        except FileNotFoundError:
            pass
    return False


def validate_harness_headless() -> None:
    """Verify the headless CLI entry and patch file exist."""
    cli = harness_paths()[0]
    patch = harness_headless_patch()
    missing = [str(p) for p in (cli, patch) if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            "Harness headless 运行时未就绪，缺少："
            f"{', '.join(missing)}"
        )


def teachmate_enabled() -> bool:
    """Return whether the normal launcher should open the TeachMate shell.

    WORKBENCH_FRONTEND=harness-dev keeps the old dual-port Harness Web mode
    for development. All other values default to the new headless mode where
    FastAPI serves the UI directly and Harness runs hidden.
    """
    value = os.getenv("WORKBENCH_FRONTEND", "teachmate").strip().lower()
    if value in {"legacy", "workbench", "0", "false", "off"}:
        return False
    # harness-dev: development mode that still launches dsh web (old path)
    if value == "harness-dev":
        return True
    # Existing desktop bundles built before the runtime step remain usable by
    # falling back to the legacy page; newly built bundles use TeachMate.
    if getattr(sys, "_MEIPASS", None) is not None and not harness_paths()[0].is_file():
        return False
    return True


def resolve_ports(default_port: int) -> tuple[int, int]:
    """Return (browser_port, internal_workbench_api_port)."""
    try:
        browser_port = int(os.getenv("TEACHMATE_UI_PORT", str(default_port)))
        backend_port = int(os.getenv("WORKBENCH_BACKEND_PORT", str(default_port + 1)))
    except ValueError as error:
        raise ValueError("TEACHMATE_UI_PORT 和 WORKBENCH_BACKEND_PORT 必须是有效端口号") from error
    if not (1 <= browser_port <= 65535 and 1 <= backend_port <= 65535):
        raise ValueError("前端和后端端口必须在 1-65535 之间")
    if browser_port == backend_port:
        raise ValueError("TeachMate 前端端口不能与 WorkBench API 端口相同")
    return browser_port, backend_port


def validate_harness_checkout() -> None:
    """Fail with a useful message instead of silently opening the old page."""
    cli, patch, dist = harness_paths()
    missing = [str(path) for path in (cli, patch, dist) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "TeachMate Web 尚未准备好，请先在 vendor/deepseek-harness-upstream 执行 pnpm run build；"
            f"缺少：{', '.join(missing)}"
        )


def find_node() -> str:
    bundled_candidates = [
        PACKAGED_RUNTIME_ROOT / "node" / "node.exe",
        PACKAGED_RUNTIME_ROOT / "node" / "node",
    ]
    candidate = os.getenv("NODE_BINARY") or next(
        (str(path) for path in bundled_candidates if path.is_file()),
        None,
    ) or shutil.which("node")
    if candidate is None:
        raise FileNotFoundError("找不到 Node.js；TeachMate Web 需要 Node.js 运行 Harness WebServer")
    return candidate


def harness_environment(*, token: str, browser_port: int, backend_port: int, data_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["WORKBENCH_TOKEN"] = token
    environment["TEACHMATE_UI_PORT"] = str(browser_port)
    environment.setdefault("TEACHMATE_MODE", "bridge")
    environment["TEACHMATE_BASE_URL"] = f"http://127.0.0.1:{backend_port}"
    environment.setdefault("DSH_HOME", str(data_dir / "harness"))
    return environment


def start_teachmate(*, token: str, browser_port: int, backend_port: int, data_dir: Path) -> subprocess.Popen:
    validate_harness_checkout()
    cli, patch, _dist = harness_paths()
    root = harness_root()
    command = [find_node(), str(cli), "web", "--patch", str(patch)]
    return subprocess.Popen(command, cwd=root, env=harness_environment(
        token=token,
        browser_port=browser_port,
        backend_port=backend_port,
        data_dir=data_dir,
    ))


def harness_headless_environment(*, token: str, backend_port: int, data_dir: Path) -> dict[str, str]:
    """Build env for headless harness: no browser port, only backend URL."""
    environment = os.environ.copy()
    environment["WORKBENCH_TOKEN"] = token
    environment.setdefault("TEACHMATE_MODE", "bridge")
    environment["TEACHMATE_BASE_URL"] = f"http://127.0.0.1:{backend_port}"
    environment.setdefault("DSH_HOME", str(data_dir / "harness"))
    environment.setdefault("AGENT_RUNTIME", "harness")
    return environment


def start_harness_headless(*, token: str, backend_port: int, data_dir: Path) -> subprocess.Popen:
    """Start a persistent headless Harness process.

    Unlike ``start_teachmate`` which runs ``dsh web`` (full Web UI),
    this runs ``dsh --profile headless`` with the teachmate education overlay.
    The process stays alive serving one-shot tasks via stdin/stdout.

    For Phase 1 prototype, we start the process in a keep-alive mode by
    giving it a no-op task. The Python adapter will spawn child processes
    per actual task.
    """
    validate_harness_headless()
    cli = harness_paths()[0]
    patch = harness_headless_patch()
    root = harness_root()
    # Use a sleep-forever task to keep the process alive.
    # The adapter will use separate one-shot invocations for actual tasks.
    command = [
        find_node(), str(cli),
        "--profile", "headless",
        "--patch", str(patch),
        "keep-alive",
    ]
    return subprocess.Popen(
        command,
        cwd=root,
        env=harness_headless_environment(
            token=token,
            backend_port=backend_port,
            data_dir=data_dir,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def start_harness_headless_task(
    *,
    token: str,
    backend_port: int,
    data_dir: Path,
    task: str,
    timeout: float = 120.0,
) -> tuple[int, str, str]:
    """Run a one-shot headless task and return (exit_code, stdout, stderr).

    This spawns a fresh ``dsh --profile headless "<task>"`` process,
    waits for it to complete, and captures the output.
    """
    validate_harness_headless()
    cli = harness_paths()[0]
    patch = harness_headless_patch()
    root = harness_root()
    command = [
        find_node(), str(cli),
        "--profile", "headless",
        "--patch", str(patch),
        task,
    ]
    env = harness_headless_environment(
        token=token,
        backend_port=backend_port,
        data_dir=data_dir,
    )
    proc = subprocess.Popen(
        command,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
        return (
            proc.returncode,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_bytes, stderr_bytes = proc.communicate()
        return (
            -1,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )


def wait_for_http(process: subprocess.Popen, url: str, *, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if 200 <= response.status < 400:
                    return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.15)
    return False


def stop_process(process: subprocess.Popen | None, *, timeout: float = 5) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
