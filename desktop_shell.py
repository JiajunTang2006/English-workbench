"""桌面宿主：窗口、菜单、单实例和浏览器回退。

桌面软件的“关闭窗口”不等于“退出服务”：窗口关闭时只隐藏，后台的
FastAPI、Worker 和 Harness 继续运行；用户从应用菜单选择“完全退出”后，
``webview.start`` 才返回，启动器再按正常生命周期停止后端。
"""

from __future__ import annotations

import atexit
import os
import socket
import sys
import time
import webbrowser
from pathlib import Path
from threading import Event, Thread, current_thread
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parent
ACTIVATION_SOCKET_NAME = ".desktop-activate.sock"


class _ActivationServer:
    """Small per-user IPC endpoint used to wake an existing app window.

    The single-instance lock prevents two backends from sharing one port, but
    a second Finder click must still have a useful effect.  The second process
    sends a ``show`` message here and exits; the running process unhides and
    focuses its native window.
    """

    def __init__(self, path: Path, callback: Callable[[], None]) -> None:
        self.path = path
        self.callback = callback
        self._stop = Event()
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if not hasattr(socket, "AF_UNIX"):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        server: socket.socket | None = None
        try:
            if self.path.exists():
                self.path.unlink()
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self.path))
            # Windows AF_UNIX addresses are kernel-managed and do not always
            # create a chmod-able filesystem entry.
            if os.name != "nt":
                os.chmod(self.path, 0o600)
            server.listen(4)
            server.settimeout(0.5)
        except (OSError, ValueError):
            try:
                if server is not None:
                    server.close()
            except (AttributeError, OSError):
                pass
            return
        self._socket = server
        self._thread = Thread(target=self._serve, name="workbench-activation", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        server = self._socket
        if server is None:
            return
        while not self._stop.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                try:
                    message = connection.recv(64).strip().lower()
                    if message == b"show":
                        self.callback()
                        connection.sendall(b"ok\n")
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        server = self._socket
        self._socket = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=1)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _activation_socket_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / ACTIVATION_SOCKET_NAME


def activate_existing_instance(data_dir: str | Path, *, retries: int = 12) -> bool:
    """Ask the already-running instance to show its window.

    A short retry window covers the small interval between acquiring the lock
    and starting the WebView IPC listener during app startup.
    """

    if hasattr(socket, "AF_UNIX"):
        path = _activation_socket_path(data_dir)
        for _ in range(max(1, retries)):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(0.25)
                    client.connect(str(path))
                    client.sendall(b"show\n")
                    return client.recv(16).strip() == b"ok"
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError):
                time.sleep(0.1)

    # Windows fallback: a hidden WebView window still exists as a native
    # top-level HWND.  This keeps repeat EXE launches useful even on systems
    # where AF_UNIX is unavailable or disabled.
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "English WorkBench")
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return True
        except (AttributeError, OSError):
            pass
    return False


class SingleInstanceLock:
    """跨 macOS/Windows 的进程锁；异常退出时由操作系统自动释放。"""

    def __init__(self, path: Path, handle) -> None:
        self.path = path
        self._handle = handle
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        try:
            self._handle.close()
        except (OSError, ValueError):
            pass

    def __enter__(self) -> "SingleInstanceLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def acquire_single_instance(data_dir: str | Path) -> SingleInstanceLock | None:
    """尝试取得当前数据目录的实例锁。

    锁文件只保存当前进程号，不保存 Token 或用户资料；不会删除旧锁文件，
    因为操作系统会在进程异常退出时自动释放文件锁。
    """

    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".desktop-instance.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write("0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return None

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    lock = SingleInstanceLock(lock_path, handle)
    atexit.register(lock.release)
    return lock


def default_icon_path() -> Path:
    """解析源码和 PyInstaller 资源目录中的托盘图标。"""

    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "app-icon.png")
        candidates.append(Path(meipass).parent / "Resources" / "assets" / "app-icon.png")
    candidates.append(PROJECT_ROOT / "assets" / "app-icon.png")
    return next((path for path in candidates if path.is_file()), candidates[-1])


class _DesktopWindow:
    def __init__(
        self,
        webview,
        window,
        *,
        on_exit: Callable[[], None] | None,
        icon_path: Path,
    ) -> None:
        self.webview = webview
        self.window = window
        self.on_exit = on_exit
        self.icon_path = icon_path
        self.allow_close = False
        self.exit_notified = False
        self.tray = None
        self.tray_thread: Thread | None = None
        self.activation: _ActivationServer | None = None

    def show(self) -> None:
        try:
            # ``restore`` also handles a window that was minimized before it
            # was hidden.  ``show`` alone is a no-op for some Cocoa backends.
            restore = getattr(self.window, "restore", None)
            if callable(restore):
                restore()
            self.window.show()
        except Exception:
            pass
        if sys.platform == "darwin":
            try:
                import AppKit  # type: ignore

                AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass

    def hide(self) -> None:
        try:
            self.window.hide()
        except Exception:
            pass

    def request_exit(self) -> None:
        self.allow_close = True
        try:
            self.window.destroy()
        except Exception:
            pass

    def on_closing(self, window=None):
        if self.allow_close:
            return True
        self.hide()
        return False

    def on_closed(self, *_args) -> None:
        if self.activation is not None:
            self.activation.stop()
            self.activation = None
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        if not self.exit_notified:
            self.exit_notified = True
            if self.on_exit is not None:
                self.on_exit()

    def start_tray(self) -> None:
        """Windows 使用托盘；macOS 使用原生应用菜单，避免 AppKit 跨线程。"""

        if os.name != "nt":
            return
        try:
            import pystray  # type: ignore
            from PIL import Image

            image = Image.open(self.icon_path) if self.icon_path.is_file() else Image.new("RGBA", (64, 64), "#1d4ed8")
            menu = pystray.Menu(
                pystray.MenuItem("打开 WorkBench", lambda *_: self.show(), default=True),
                pystray.MenuItem("完全退出", lambda *_: self.request_exit()),
            )
            self.tray = pystray.Icon("EnglishWorkBench", image, "English WorkBench", menu)
            self.tray_thread = Thread(target=self.tray.run, name="workbench-tray", daemon=True)
            self.tray_thread.start()
        except Exception:
            self.tray = None

    def start_activation(self, data_dir: str | Path) -> None:
        activation = _ActivationServer(_activation_socket_path(data_dir), self.show)
        activation.start()
        self.activation = activation


def _install_macos_lifecycle_delegate(controller: _DesktopWindow) -> None:
    """Teach Cocoa how to reopen a hidden window and quit gracefully.

    pywebview's default Cocoa delegate asks every window whether it may close
    when the user chooses Quit.  Our normal close handler deliberately returns
    ``False`` so the red window button hides the app, which also caused Cmd+Q
    to be cancelled.  A dedicated app delegate keeps those two intents
    separate: Dock/Finder reopen shows the window, while Quit requests a real
    close and lets the launcher stop the backend.
    """

    if sys.platform != "darwin":
        return
    try:
        import Foundation  # type: ignore
        from webview.platforms import cocoa  # type: ignore

        base_delegate = cocoa.BrowserView.AppDelegate

        class WorkbenchAppDelegate(base_delegate):
            def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible_windows):
                controller.show()
                return Foundation.YES

            def applicationShouldTerminate_(self, app):
                # Cancel Cocoa's immediate termination and close through the
                # normal launcher path so Uvicorn/Harness can stop cleanly.
                controller.request_exit()
                return Foundation.NO

        cocoa.BrowserView.AppDelegate = WorkbenchAppDelegate
    except Exception as exc:
        # The Unix-socket activation channel still supports explicit second
        # launches if the native delegate cannot be installed.
        print(f"macOS 生命周期事件接管失败：{exc}")


def open_desktop_window(
    url: str,
    *,
    title: str = "English WorkBench",
    on_exit: Callable[[], None] | None = None,
    data_dir: str | Path | None = None,
) -> bool:
    """打开常驻桌面窗口，返回是否成功使用 WebView。

    关闭窗口只隐藏；应用菜单中的“完全退出”才会结束 ``webview.start``。
    未安装 pywebview 或原生 GUI 后端不可用时回退到默认浏览器。
    """

    try:
        import webview  # type: ignore
        from webview.menu import Menu, MenuAction, MenuSeparator
    except ImportError:
        print("未安装 pywebview，回退到默认浏览器。")
        webbrowser.open(url)
        return False

    controller: _DesktopWindow | None = None

    def show_window() -> None:
        if controller is not None:
            controller.show()

    def exit_window() -> None:
        if controller is not None:
            controller.request_exit()

    menu = [
        Menu(
            "WorkBench",
            [
                MenuAction("显示窗口", show_window),
                MenuSeparator(),
                MenuAction("完全退出", exit_window),
            ],
        )
    ]

    try:
        window = webview.create_window(
            title,
            url,
            width=1440,
            height=920,
            min_size=(1024, 700),
            resizable=True,
            text_select=True,
            menu=menu,
        )
        if window is None:
            raise RuntimeError("pywebview 未创建窗口")
        controller = _DesktopWindow(
            webview,
            window,
            on_exit=on_exit,
            icon_path=default_icon_path(),
        )
        window.events.closing += controller.on_closing
        window.events.closed += controller.on_closed
        _install_macos_lifecycle_delegate(controller)
        if data_dir is not None:
            controller.start_activation(data_dir)
        controller.start_tray()
        debug = os.getenv("WORKBENCH_WEBVIEW_DEBUG", "").lower() in {"1", "true", "yes"}
        webview.start(debug=debug)
        return True
    except Exception as exc:  # pragma: no cover - depends on native GUI backend
        if controller is not None and controller.activation is not None:
            controller.activation.stop()
            controller.activation = None
        if controller is not None and controller.tray is not None:
            try:
                controller.tray.stop()
            except Exception:
                pass
        print(f"内嵌桌面窗口启动失败，回退到默认浏览器：{exc}")
        webbrowser.open(url)
        return False
