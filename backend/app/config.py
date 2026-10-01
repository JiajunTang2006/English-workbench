from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "TeachMate 教学工作台"
DATA_DIR_NAME = "workbench"
# Keep reading installations created before the neutral English rename.  Unicode
# escapes prevent the former personal name from appearing in project metadata.
LEGACY_DATA_DIR_NAMES = ("\u51af\u8001\u5e08\u521d\u4e2d\u82f1\u8bed\u5de5\u4f5c\u53f0",)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'workbench.db'}"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def imports_dir(self) -> Path:
        return self.data_dir / "imports"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.backups_dir, self.imports_dir,
                          self.exports_dir, self.attachments_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)


def default_data_dir() -> Path:
    override = os.getenv("WORKBENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    current = base / DATA_DIR_NAME
    if current.exists():
        return current
    for legacy_name in LEGACY_DATA_DIR_NAMES:
        legacy = base / legacy_name
        if legacy.exists():
            return legacy
    return current


def get_settings() -> Settings:
    host = os.getenv("WORKBENCH_HOST", DEFAULT_HOST)
    try:
        port = int(os.getenv("WORKBENCH_PORT", str(DEFAULT_PORT)))
    except ValueError as error:
        raise ValueError("WORKBENCH_PORT 必须是有效端口号") from error
    if not (1 <= port <= 65535):
        raise ValueError("WORKBENCH_PORT 必须在1到65535之间")
    settings = Settings(data_dir=default_data_dir(), host=host, port=port)
    settings.ensure_directories()
    return settings
