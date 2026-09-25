"""MCP 错误码与插件错误分类映射。"""
from __future__ import annotations

# JSON-RPC / MCP 标准错误码
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

# 插件侧业务错误码 → 人类可读类别
PLUGIN_ERROR_LABELS = {
    "plugin_disabled": "插件未启用",
    "unauthenticated": "未认证或令牌无效",
    "forbidden_scope": "令牌权限不足",
    "not_found": "资源不存在或越权",
    "api_version_unsupported": "API 版本不兼容",
    "invalid_pairing_code": "配对码无效或过期",
    "data_incomplete": "数据不完整",
    "service_unavailable": "TeachMate 服务未启动",
    "http_error": "服务端错误",
}


def label_for(code: str | None) -> str:
    if not code:
        return "未知错误"
    return PLUGIN_ERROR_LABELS.get(code, code)
