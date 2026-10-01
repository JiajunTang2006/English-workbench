"""MCP 工具返回值的通用解码。

不同学校的 MCP 服务把 JSONL/JSON 包在 MCP ``content[].text`` 里的方式不完全一致：
有的整段返回一个 JSON 数组，有的按行返回 JSONL，有的把结果塞进 ``{data: [...]}``。
这里只做「拿到一批行」这一件事，供 MONI 与自定义数据源共用，避免两份解码逻辑。
"""

from __future__ import annotations

import json
from typing import Any

# 常见的包裹字段：服务端可能把真正的行数组放在这些键下面。
_ROW_CONTAINERS = ("rows", "data", "items", "students", "results", "records", "list")


def mcp_text(result: dict[str, Any]) -> str:
    """把 MCP 工具返回值里的文本片段拼起来；没有文本时视为空结果。"""
    chunks = [
        item.get("text", "")
        for item in result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "\n".join(chunk for chunk in chunks if chunk)


def mcp_rows(text: str) -> list[dict[str, Any]]:
    """把 MCP 返回的文本解析成「一批字典行」。

    依次尝试：整段 JSON、逐行 JSONL、常见包裹字段；无法解析时返回空列表，
    由调用方决定是报错还是跳过（缺失证据不补 0 的约定在这里也适用）。
    """
    if not text or not text.strip():
        return []
    value: Any = None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                value.append(parsed)
    if isinstance(value, dict):
        for key in _ROW_CONTAINERS:
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []
