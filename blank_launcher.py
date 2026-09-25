"""Blank distribution launcher shared by the macOS app and Windows build.

The blank distribution deliberately uses a separate per-user data directory so
that an existing Workbench database is never opened by accident.
"""
from __future__ import annotations

import os
from pathlib import Path


def configure_blank_data_dir() -> None:
    if os.getenv("WORKBENCH_DATA_DIR"):
        return
    if os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home()))
    elif os.sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    os.environ["WORKBENCH_DATA_DIR"] = str((base / "English Workbench Blank").resolve())


def main() -> int:
    configure_blank_data_dir()
    from windows_launcher import main as launch_server

    return launch_server()


if __name__ == "__main__":
    raise SystemExit(main())
