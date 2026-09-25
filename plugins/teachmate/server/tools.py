"""MCP 工具调度：把工具名 + 参数翻译成对 FastAPI 的调用，并格式化结果。"""
from __future__ import annotations

import json

from . import error
from .client import PluginClientError
from .schemas import TOOL_ROUTES


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _fail(code: str, message: str) -> dict:
    return {"content": [{"type": "text", "text": f"[{code}] {message}"}], "isError": True}


def _format(name: str, data: dict) -> str:
    if name == "get_teachmate_status":
        caps = ", ".join(data.get("capabilities", []))
        return (f"TeachMate 插件状态：{data.get('health')} | 版本 {data.get('app_version')} "
                f"| schema {data.get('schema')} | api_version {data.get('api_version')}\n"
                f"可用工具({len(data.get('capabilities', []))})：{caps}")
    return json.dumps(data, ensure_ascii=False, indent=2)


def dispatch(name: str, arguments: dict, client) -> dict:
    route = TOOL_ROUTES.get(name)
    if route is None:
        return _fail("unknown_tool", f"未知工具：{name}")
    method, path_tpl, path_keys, query_keys = route
    try:
        path = path_tpl
        for k in path_keys:
            if arguments.get(k) is None:
                return _fail("invalid_arguments", f"缺少路径参数：{k}")
            path = path.replace("{" + k + "}", str(arguments[k]))
        params = {k: arguments[k] for k in query_keys if arguments.get(k) is not None}
        data = client.call(method, path, params)
    except PluginClientError as e:
        return _fail(e.code or "plugin_error", e.message or str(e))
    except Exception as e:  # 不应发生，但避免整服务崩溃
        return _fail("client_error", str(e))
    return _ok(_format(name, data))
