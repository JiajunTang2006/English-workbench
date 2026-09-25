"""Codex 插件 MCP stdio 服务器入口（标准库实现，零依赖）。

Codex 通过 stdio 启动本进程；本进程用回环地址调用 TeachMate FastAPI。
支持两种运行方式：
  1) 无参数           → 启动 stdio MCP 服务器；
  2) pair --code X --teacher-token Y → 一次性配对并保存插件令牌后退出。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import error
from .auth import load_token, pair, save_token
from .client import PluginClient
from .schemas import API_VERSION, TOOL_SPECS
from .tools import _fail, dispatch

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "teachmate-codex-plugin"
SERVER_VERSION = "0.1.3-local"
DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def _build_client():
    token = load_token()
    if not token:
        return None
    base_url = os.getenv("TEACHMATE_PLUGIN_BASE_URL", DEFAULT_BASE_URL)
    return PluginClient(base_url, token, api_version=API_VERSION)


def _respond(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def _respond_error(msg_id, code, message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                  "error": {"code": code, "message": message}}) + "\n")
    sys.stdout.flush()


def handle(msg: dict, client) -> None:
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        _respond(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return
    if method == "ping":
        _respond(msg_id, {})
        return
    if method == "tools/list":
        _respond(msg_id, {"tools": TOOL_SPECS})
        return
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if client is None:
            _respond(msg_id, _fail("unauthenticated", "插件尚未配对，请先运行配对流程"))
            return
        _respond(msg_id, dispatch(name, arguments, client))
        return
    if method and method.startswith("notifications/"):
        return  # 通知无需响应
    _respond_error(msg_id, error.JSONRPC_METHOD_NOT_FOUND, f"未知方法：{method}")


def run_stdio():
    client = _build_client()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        try:
            handle(msg, client)
        except Exception as e:  # 单条消息异常不应终止服务
            _respond_error(msg.get("id"), error.JSONRPC_INTERNAL_ERROR, str(e))


def run_pair(args):
    base_url = args.base_url or os.getenv("TEACHMATE_PLUGIN_BASE_URL", DEFAULT_BASE_URL)
    if not args.teacher_token:
        args.teacher_token = os.getenv("WORKBENCH_TOKEN", "")
    if not args.teacher_token:
        print("错误：缺少教师令牌（--teacher-token 或环境变量 WORKBENCH_TOKEN）", file=sys.stderr)
        return 2
    try:
        token = pair(base_url, args.code, args.teacher_token)
    except RuntimeError as e:
        print(f"配对失败：{e}", file=sys.stderr)
        return 1
    print(f"配对成功，插件令牌已保存（scope=teaching.read）。")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="teachmate-codex-plugin")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("pair", help="用配对码兑换并保存插件令牌")
    p.add_argument("--code", required=True, help="TeachMate 设置页生成的一次性配对码")
    p.add_argument("--teacher-token", default=None, help="教师本地令牌（WORKBENCH_TOKEN）")
    p.add_argument("--base-url", default=None, help="TeachMate API 基址")
    args = parser.parse_args()
    if args.cmd == "pair":
        return run_pair(args)
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
