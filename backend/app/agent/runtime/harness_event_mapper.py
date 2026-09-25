"""Harness 事件映射器

将 Harness session 事件（turn/start, step/start, assistant/chunk, tool/call,
tool/result, turn/end 等）映射为项目共享的 RuntimeEvent。

映射规则：
    turn/start    → run.started
    step/start    → model.started
    assistant/chunk → model.delta
    tool/call     → tool.started
    tool/result   → tool.completed
    turn/end      → run.completed (正常) 或 run.failed (异常原因)

Harness 事件通过 HTTP API 以 JSON 数组形式返回，每个事件包含：
    { "type": "turn/start", "seq": 1, "time": 16921..., "data": { "turn": 0 } }

设计决策：
    - 映射器是无状态函数集合，不持有运行时状态
    - 未知事件类型被忽略（Harness 的 ignorable 机制保证前向兼容）
    - turn/end 的 reason 决定终态事件类型
    - evidence_id 从 tool/result 的 education 工具输出中提取
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..event_types import (
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_IDLE,
    RUN_DEGRADED,
    MODEL_STARTED,
    MODEL_DELTA,
    MODEL_COMPLETED,
    TOOL_STARTED,
    TOOL_COMPLETED,
    USAGE_UPDATED,
)
from .base import RuntimeEvent

logger = logging.getLogger(__name__)


# Harness 事件类型常量（与 known-event-types.ts 对齐）
H_TURN_START = "turn/start"
H_TURN_END = "turn/end"
H_STEP_START = "step/start"
H_STEP_END = "step/end"
H_ASSISTANT_CHUNK = "assistant/chunk"
H_ASSISTANT_MESSAGE = "assistant/message"
H_TOOL_CALL = "tool/call"
H_TOOL_RESULT = "tool/result"
H_SESSION_TITLE = "session/title"
H_AGENT_STATUS = "agent/status"
H_TOKEN_METER = "token-meter/update"

# turn/end reason kinds that indicate failure
_FAILURE_REASONS = frozenset({"error", "interrupted", "cancelled"})

# turn/end reason kinds that indicate degradation (partial result)
_DEGRADED_REASONS = frozenset({"degraded", "partial", "fallback", "truncated"})


def _public_progress_fields(data: dict[str, Any]) -> dict[str, Any]:
    """保留运行时明确标注的公开进度字段，不透传 prompt/reasoning/output。"""
    result: dict[str, Any] = {}
    aliases = {
        "title": ("title", "label"),
        "summary": ("summary", "detail"),
        "next_action": ("next_action", "nextAction", "next"),
        "step_id": ("step_id", "stepId", "id"),
        "order": ("order",),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = data.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                result[target] = value
                break
    return result


def map_harness_event(raw: dict[str, Any]) -> RuntimeEvent | None:
    """将单个 Harness session 事件映射为 RuntimeEvent。

    :param raw: 原始 Harness 事件，形如 {"type": "turn/start", "seq": 1, "time": ..., "data": {...}}
    :return: 映射后的 RuntimeEvent，若事件类型不需要映射则返回 None
    """
    event_type = raw.get("type", "")
    data = raw.get("data", {})
    # Harness 时间戳是毫秒级 Unix epoch
    ts_ms = raw.get("time", 0)
    timestamp = ts_ms / 1000.0 if ts_ms else time.time()

    if event_type == H_TURN_START:
        turn_data = {
            "turn": data.get("turn", 0),
            "session_id": str(data.get("session_id", "")),
        }
        turn_data.update(_public_progress_fields(data))
        return RuntimeEvent(
            event_type=RUN_STARTED,
            data=turn_data,
            timestamp=timestamp,
        )

    if event_type == H_STEP_START:
        step_data = {
            "turn": data.get("turn", 0),
            "step": data.get("step", 0),
        }
        step_data.update(_public_progress_fields(data))
        return RuntimeEvent(
            event_type=MODEL_STARTED,
            data=step_data,
            timestamp=timestamp,
        )

    if event_type == H_STEP_END:
        return RuntimeEvent(
            event_type=MODEL_COMPLETED,
            data={
                "turn": data.get("turn", 0),
                "step": data.get("step", 0),
            },
            timestamp=timestamp,
        )

    if event_type == H_AGENT_STATUS:
        status = data.get("status", "")
        if status == "idle":
            return RuntimeEvent(
                event_type=RUN_IDLE,
                data={
                    "session_id": str(data.get("session_id", "")),
                },
                timestamp=timestamp,
            )
        # 其他 agent/status 子类型（thinking, working 等）暂不映射
        return None

    if event_type == H_ASSISTANT_MESSAGE:
        message = data.get("message", {})
        return RuntimeEvent(
            event_type=MODEL_COMPLETED,
            data={
                "turn": data.get("turn", 0),
                "step": data.get("step", 0),
                "content": message.get("content", ""),
            },
            timestamp=timestamp,
        )

    if event_type == H_TOKEN_METER:
        meter = data.get("meter", data)
        # U3-04: 提取 model_name / provider / provider_request_id
        # 用于写入 llm_usage_records，不记录 API Key
        cost_yuan = meter.get("costYuan", meter.get("cost_yuan"))
        # cost_yuan 可能为 null/undefined（未知模型定价）→ 保留为 None
        if cost_yuan is not None:
            try:
                cost_yuan = float(cost_yuan)
            except (ValueError, TypeError):
                cost_yuan = None
        event_data: dict[str, Any] = {
            "input_tokens": meter.get("inputTokens", meter.get("input_tokens", 0)),
            "cache_read_tokens": meter.get("cacheReadTokens", meter.get("cache_read_tokens", 0)),
            "reasoning_tokens": meter.get("reasoningTokens", meter.get("reasoning_tokens", 0)),
            "output_tokens": meter.get("outputTokens", meter.get("output_tokens", 0)),
            "total_tokens": meter.get("totalTokens", meter.get("total_tokens", 0)),
            "cost_yuan": cost_yuan,
            "model_name": meter.get("modelName", meter.get("model_name", "unknown")),
            "provider": meter.get("provider", "unknown"),
        }
        request_id = meter.get("requestId", meter.get("request_id"))
        if request_id:
            event_data["provider_request_id"] = str(request_id)
        return RuntimeEvent(
            event_type=USAGE_UPDATED,
            data=event_data,
            timestamp=timestamp,
        )

    if event_type == H_ASSISTANT_CHUNK:
        chunk = data.get("chunk", {})
        return RuntimeEvent(
            event_type=MODEL_DELTA,
            data={
                "turn": data.get("turn", 0),
                "step": data.get("step", 0),
                "delta": _extract_chunk_text(chunk),
            },
            timestamp=timestamp,
        )

    if event_type == H_TOOL_CALL:
        tool_data = {
            "turn": data.get("turn", 0),
            "step": data.get("step", 0),
            "call_id": str(data.get("callId", "")),
            "tool_name": data.get("name", ""),
            "tool": data.get("name", ""),
        }
        tool_data.update(_public_progress_fields(data))
        return RuntimeEvent(
            event_type=TOOL_STARTED,
            data=tool_data,
            timestamp=timestamp,
        )

    if event_type == H_TOOL_RESULT:
        message = data.get("message", {})
        content = message.get("content", [])
        tool_name = str(
            data.get("name") or data.get("toolName") or data.get("tool_name") or ""
        )
        result_text = ""
        evidence_id = None

        # 提取工具名和结果文本
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    result_text = item.get("text", "")
                    break

        # 从教育工具结果中提取 evidence_id
        if result_text:
            try:
                import json
                parsed = json.loads(result_text)
                if isinstance(parsed, dict) and "evidenceId" in parsed:
                    evidence_id = parsed["evidenceId"]
            except (ValueError, TypeError):
                pass

        event_data: dict[str, Any] = {
            "turn": data.get("turn", 0),
            "step": data.get("step", 0),
            "call_id": str(data.get("callId", "")),
            "tool_name": tool_name,
            "tool": tool_name,
            "is_error": bool(data.get("error")),
        }
        event_data.update(_public_progress_fields(data))
        if evidence_id:
            event_data["evidence_id"] = evidence_id

        return RuntimeEvent(
            event_type=TOOL_COMPLETED,
            data=event_data,
            timestamp=timestamp,
        )

    if event_type == H_TURN_END:
        reason = data.get("reason", {})
        reason_kind = reason.get("kind", "") if isinstance(reason, dict) else str(reason)

        if reason_kind in _FAILURE_REASONS:
            return RuntimeEvent(
                event_type=RUN_FAILED,
                data={
                    "turn": data.get("turn", 0),
                    "reason": reason_kind,
                },
                timestamp=timestamp,
            )

        if reason_kind in _DEGRADED_REASONS:
            return RuntimeEvent(
                event_type=RUN_DEGRADED,
                data={
                    "turn": data.get("turn", 0),
                    "reason": reason_kind,
                },
                timestamp=timestamp,
            )

        return RuntimeEvent(
            event_type=RUN_COMPLETED,
            data={
                "turn": data.get("turn", 0),
                "reason": reason_kind or "completed",
            },
            timestamp=timestamp,
        )

    # 未知事件类型 — 忽略（Harness 的 ignorable 机制保证前向兼容）
    logger.debug("Harness event type '%s' has no mapping, ignoring", event_type)
    return None


def map_harness_events(raw_events: list[dict[str, Any]]) -> list[RuntimeEvent]:
    """批量映射 Harness 事件列表。

    :param raw_events: Harness 事件列表
    :return: 映射后的 RuntimeEvent 列表（过滤掉 None）
    """
    results: list[RuntimeEvent] = []
    for raw in raw_events:
        mapped = map_harness_event(raw)
        if mapped is not None:
            results.append(mapped)
    return results


def _extract_chunk_text(chunk: dict[str, Any]) -> str:
    """从 StreamChunk 中提取文本增量。

    Harness 的 StreamChunk 可能包含不同类型的增量：
    - text-delta: {"type": "text-delta", "text": "..."}
    - 其他类型（reasoning, tool-call 等）不映射为文本
    """
    chunk_type = chunk.get("type", "")
    if chunk_type == "text-delta":
        return chunk.get("text", "")
    return ""
