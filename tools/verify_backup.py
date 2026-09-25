from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.app.services.backups import create_backup, sha256, verify_backup


__all__ = ["create_backup", "sha256", "verify_backup"]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python tools/verify_backup.py <备份目录>")
    print(json.dumps(verify_backup(Path(sys.argv[1])), ensure_ascii=False, indent=2))
