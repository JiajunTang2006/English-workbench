"""Blank distribution launcher shared by the macOS app and Windows build.

The blank distribution deliberately uses a separate per-user data directory so
that an existing Workbench database is never opened by accident.
"""
from __future__ import annotations

import os
import sys
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
    # The education tools invoke sys.executable with this exact module. In a
    # frozen app it is the app executable, not a general Python interpreter.
    # Dispatch only the bundled bridge so tools return JSON rather than launch
    # another desktop window. No arbitrary module execution is allowed.
    if len(sys.argv) > 1 and sys.argv[1] == "-m":
        for name, descriptor, mode in (("stdin", 0, "r"), ("stdout", 1, "w"), ("stderr", 2, "w")):
            if getattr(sys, name) is None:
                setattr(sys, name, os.fdopen(os.dup(descriptor), mode, encoding="utf-8"))
        if len(sys.argv) < 3 or sys.argv[2] != "backend.app.agent.education_bridge.bridge_cli":
            print("Unsupported desktop helper module", file=sys.stderr)
            return 2
        from backend.app.agent.education_bridge.bridge_cli import main as bridge_main
        bridge_main(sys.argv[3:])
        return 0
    configure_blank_data_dir()
    from windows_launcher import main as launch_server

    return launch_server()


if __name__ == "__main__":
    raise SystemExit(main())
