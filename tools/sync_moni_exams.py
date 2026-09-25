#!/usr/bin/env python3
"""同步 MONI 当前学期考试、成绩和题目级分析字段到 Workbench。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.services.moni_sync import sync_current_term


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 MONI 当前学期考试数据")
    parser.add_argument("--dry-run", action="store_true", help="只读取和统计，不写入数据库")
    args = parser.parse_args()
    try:
        print(json.dumps(sync_current_term(get_settings(), dry_run=args.dry_run), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"MONI考试同步失败：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
