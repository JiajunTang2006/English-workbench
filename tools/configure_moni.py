#!/usr/bin/env python3
"""安全保存 MONI MCP Bearer 令牌到本机 Workbench 数据目录。"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.agent.keyvault import clear_api_key, save_api_key


def main() -> int:
    token = getpass.getpass("MONI MCP Bearer token（输入不回显）：").strip()
    if not token:
        clear_api_key("moni")
        print("已清除 MONI 令牌。")
        return 0
    save_api_key(token, "moni")
    print("MONI 令牌已保存到本机应用数据目录（权限 0600）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
