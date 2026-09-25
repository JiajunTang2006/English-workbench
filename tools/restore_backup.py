from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.services.backups import restore_backup


def main() -> int:
    parser = argparse.ArgumentParser(description="离线恢复 English Workbench 备份")
    parser.add_argument("backup", type=Path, help="已验证的备份目录")
    parser.add_argument("--data-dir", type=Path, required=True, help="正式数据目录（必须先停止应用）")
    args = parser.parse_args()
    safety_dir = restore_backup(args.backup, args.data_dir)
    print(json.dumps({"restored": str(args.data_dir.resolve()), "safety_copy": str(safety_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
