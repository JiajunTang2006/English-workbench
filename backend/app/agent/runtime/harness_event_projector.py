"""
HarnessEventProjector — 事件投影器 (U1-02 / B2-01)

将 Harness JSON-RPC 运行时通知投影为后端内部 RuntimeEvent。
职责：
1. 接收原始 JSON-RPC notification（vendored SDK 客户端回调：method + payload）
2. 映射为 backend.app.agent.runtime.base.RuntimeEvent
3. 转交给事件投影（analysis_run_events 落库 + 活跃 TaskRegistry 广播）

真实 SDK 通知格式（与协议实现对齐）：
- ``session.event``:  {"sessionId": str, "event": {"type": str, "data": {...}}}
- ``session.status``: {"sessionId": str, "status": "idle" | ...}
- ``subagent.started/finished``: {"parentSessionId": ..., "childSessionId": ...}

高频且无审计价值的内部事件（agent/inbox/spliced 回执、流式 delta）被过滤，
只投影对运行状态有意义的稳定事件，避免 analysis_run_events 爆炸。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from ...agent.runtime.base import RuntimeEvent
from ..event_types import (
    MODEL_STARTED,
    MODEL_COMPLETED,
    MODEL_THINKING,
    PROGRESS_STEP_STARTED,
    PROGRESS_STEP_UPDATED,
    PROGRESS_STEP_COMPLETED,
    PROGRESS_HEARTBEAT,
    TOOL_STARTED,
    TOOL_COMPLETED,
)

logger = logging.getLogger(__name__)

# 不落库的内部/流式事件（保持 analysis_run_events 数字可审计）
_FILTER_TYPES = frozenset({
    "agent/inbox/spliced",   # session/prompt 回执（SDK 内部同步机制）
    "message.delta",         # 流式增量
    "thinking.delta",
    "tool_call.delta",
    "assistant/chunk",       # 流式增量 chunk（含 reasoning/text/tool-call/finish）
    "reasoning-chunks",
    "text-chunks",
    "tool-call-chunks",
})

# 深度思考增量节流：至少 _THINKING_FLUSH_CHARS 字符 或 _THINKING_FLUSH_INTERVAL 秒
# 才 flush 一条 MODEL_THINKING 事件（避免逐 token 刷屏 DB/SSE）。
_THINKING_FLUSH_CHARS = 64
_THINKING_FLUSH_INTERVAL = 0.6


class HarnessEventProjector:
    """
    事件投影器：把 Harness 通知转换为后端 RuntimeEvent。

    支持的方法类型（真实协议）：
    - session.event: 会话级事件（assistant/message、turn/start、turn/end、tool 等）
    - session.status: 会话状态（idle/finished/cancelled）
    - usage.update: 用量更新（兼容旧格式，保留）
    - error: 错误通知（兼容旧格式，保留）
    """

    def __init__(self):
        self._handlers: List[Callable[[RuntimeEvent], None]] = []
        # 深度思考增量缓冲（sessionId → 累积文本），配合节流 flush 广播。
        self._thinking_buf: Dict[str, str] = {}
        self._thinking_flush_at: Dict[str, float] = {}

    def on_event(self, handler: Callable[[RuntimeEvent], None]) -> None:
        """注册事件处理器。"""
        self._handlers.append(handler)

    def _dispatch(self, event: RuntimeEvent) -> None:
        """把事件分发给所有已注册处理器。"""
        for handler in self._handlers:
            try:
                handler(event)
            except Exception as e:
                logger.warning("Event handler error: %s", e)

    def flush_thinking(self, session_id: str | None) -> None:
        """flush 该会话未广播的思考增量（模型回答完成/回合结束时调用）。"""
        sid = str(session_id or "")
        if not sid:
            return
        buf = self._thinking_buf.pop(sid, "")
        self._thinking_flush_at.pop(sid, None)
        if buf.strip():
            self._dispatch(RuntimeEvent(
                event_type=MODEL_THINKING,
                data={
                    "sessionId": sid,
                    "delta": buf,
                    # 累计思考字数：教师端"深度思考"行的真实计量来源（不暴露原文）
                    "thinking_chars": len(buf),
                },
            ))

    def _accumulate_thinking(self, session_id: str | None, text: str) -> None:
        """累积 reasoning-delta，达到节流阈值时广播一条 MODEL_THINKING。"""
        sid = str(session_id or "")
        if not sid or not text:
            return
        buf = self._thinking_buf.get(sid, "") + text
        now = time.time()
        last = self._thinking_flush_at.get(sid, 0.0)
        if len(buf) >= _THINKING_FLUSH_CHARS or (now - last) >= _THINKING_FLUSH_INTERVAL:
            self._dispatch(RuntimeEvent(
                event_type=MODEL_THINKING,
                data={
                    "sessionId": sid,
                    "delta": buf,
                    # 本批思考字数（教师端累加得到真实"已思考 N 字"，不含原文）
                    "thinking_chars": len(buf),
                },
            ))
            self._thinking_buf[sid] = ""
            self._thinking_flush_at[sid] = now
        else:
            self._thinking_buf[sid] = buf

    def project(self, method: str, params: Dict[str, Any]) -> Optional[RuntimeEvent]:
        """
        把 notification (method, payload) 投影为 RuntimeEvent 并分发给处理器。
        返回投影出的 RuntimeEvent，或 None（无法识别/被过滤）。
        """
        event = self._map(method, params)
        if event is None:
            logger.debug("Unmapped/filtered harness event: %s", method)
            return None

        self._dispatch(event)
        return event

    def _map(self, method: str, params: Dict[str, Any]) -> Optional[RuntimeEvent]:
        """映射 Harness 事件为 RuntimeEvent。"""
        if method == "session.event":
            return self._map_session_event(params)
        elif method == "session.status":
            return self._map_session_status(params)
        elif method == "usage.update":
            return self._map_usage(params)
        elif method == "error":
            return self._map_error(params)
        else:
            return None

    def _map_session_event(self, params: Dict[str, Any]) -> Optional[RuntimeEvent]:
        """映射 session.event。

        真实格式：{"sessionId": ..., "event": {"type": ..., "data": {...}}}；
        兼容旧格式：{"type": ..., "data": ...}。
        """
        session_id = params.get("sessionId") or params.get("session_id")
        inner = params.get("event")
        if isinstance(inner, dict):
            event_type = inner.get("type", "")
            event_data = inner.get("data") or {}
        else:
            # 旧格式：type/data 直接在 params
            event_type = params.get("type", "")
            event_data = params.get("data") or {}

        # 深度思考增量（assistant/chunk 中 reasoning-delta 类型）：
        # 提取后节流广播 MODEL_THINKING，原始 chunk 仍不落库。
        if event_type == "assistant/chunk":
            chunk = event_data.get("chunk") if isinstance(event_data, dict) else None
            if isinstance(chunk, dict) and chunk.get("type") == "reasoning-delta":
                self._accumulate_thinking(session_id, str(chunk.get("text") or ""))
            return None

        if event_type in _FILTER_TYPES:
            return None
        if not event_type:
            return None
        if event_type in (
            "assistant/message", "message.start", "assistant.message",
        ):
            event_type = "message_start"
        elif event_type in ("turn/start", "turn.start"):
            event_type = "turn_start"
        elif event_type in ("turn/end", "turn.end"):
            event_type = "turn_end"
        elif event_type in ("step/start", "step.start"):
            # step/start、step/end 是 Harness 实际模型工作流的边界。
            # 之前它们在 _FILTER_TYPES 中被提前丢弃，导致教师端只能看到
            # run.started，无法实时知道模型何时开始/结束一个推理步骤。
            event_type = MODEL_STARTED
        elif event_type in ("step/end", "step.end"):
            event_type = MODEL_COMPLETED
        elif event_type in ("tool/start", "tool_call.start", "tool/call"):
            # 必须是 event_types.TOOL_STARTED（"tool.started"）——前端
            # EVENT_TYPES 只认点号名，旧的 "tool_call_start" 字面量永远匹配
            # 不上，导致工具步骤完全不进教师端过程时间线。
            event_type = TOOL_STARTED
        elif event_type in ("tool/end", "tool_call.end", "tool/result"):
            event_type = TOOL_COMPLETED
        elif event_type in ("agent/finished", "agent/end"):
            event_type = "agent_finished"
        elif event_type in ("progress.step_started", "progress/step_started", "progress.start"):
            event_type = PROGRESS_STEP_STARTED
        elif event_type in ("progress.step_updated", "progress/step_updated", "progress.update"):
            event_type = PROGRESS_STEP_UPDATED
        elif event_type in ("progress.step_completed", "progress/step_completed", "progress.complete"):
            event_type = PROGRESS_STEP_COMPLETED
        elif event_type in ("progress.heartbeat", "progress/heartbeat"):
            event_type = PROGRESS_HEARTBEAT

        data: Dict[str, Any] = dict(event_data) if isinstance(event_data, dict) else {}
        # 公开进度事件只允许白名单字段，避免把同一通知里的 reasoning/prompt
        # 或工具原始输出误广播给教师端。
        if event_type in (PROGRESS_STEP_STARTED, PROGRESS_STEP_UPDATED, PROGRESS_STEP_COMPLETED):
            allowed = {"step_id", "stepId", "id", "title", "label", "summary", "detail",
                       "next_action", "nextAction", "next", "order", "status"}
            data = {key: value for key, value in data.items() if key in allowed}
        # 工具事件统一保留公开名称，供聊天内进度卡配对 started/completed。
        if event_type in (TOOL_STARTED, TOOL_COMPLETED):
            tool_name = data.get("tool_name") or data.get("toolName") or data.get("name") or data.get("tool") or ""
            data.setdefault("tool_name", str(tool_name))
            data.setdefault("tool", str(tool_name))
        # 供事件投影按 harness 会话反查 run_id
        if session_id:
            data.setdefault("sessionId", str(session_id))
            data.setdefault("harnessSessionId", str(session_id))

        # 真实引擎不在 usage.update 发用量；用量随 assistant/message 的
        # data.usage 返回（inputTokens/outputTokens/cacheReadTokens…）。
        # 提取成独立 usage_updated 事件，供 llm_usage_records 落库审计。
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if raw_usage and event_type == "message_start":
            usage_event = RuntimeEvent(
                event_type="usage_updated",
                data={
                    "input_tokens": raw_usage.get("inputTokens")
                    or raw_usage.get("input_tokens", 0),
                    "cache_read_tokens": raw_usage.get("cacheReadTokens")
                    or raw_usage.get("cache_read_tokens", 0),
                    "reasoning_tokens": raw_usage.get("reasoningTokens")
                    or raw_usage.get("reasoning_tokens", 0),
                    "output_tokens": raw_usage.get("outputTokens")
                    or raw_usage.get("output_tokens", 0),
                    "total_tokens": raw_usage.get("totalTokens")
                    or raw_usage.get("total_tokens", 0),
                    "model_name": raw_usage.get("model") or "",
                    "provider": "deepseek",
                    "sessionId": str(session_id) if session_id else "",
                },
            )
            self._dispatch(usage_event)

        # 模型回答完成（assistant/message）或回合结束（turn/end）时，
        # 把剩余未广播的思考增量 flush 掉，避免尾部思考文本丢失。
        if event_type in ("message_start", "turn_end"):
            self.flush_thinking(session_id)

        return RuntimeEvent(event_type=event_type, data=data)

    def _map_session_status(self, params: Dict[str, Any]) -> Optional[RuntimeEvent]:
        """映射 session.status（idle / finished / cancelled）。"""
        session_id = params.get("sessionId") or params.get("session_id")
        status = params.get("status", "")
        if not status:
            return None
        data: Dict[str, Any] = {
            "status": status,
            "sessionId": str(session_id) if session_id else "",
        }
        return RuntimeEvent(event_type=f"harness_{status}", data=data)

    def _map_usage(self, params: Dict[str, Any]) -> Optional[RuntimeEvent]:
        """映射 usage.update。"""
        # 保留 model_name / provider / provider_request_id / cost_yuan
        # 用于写入 llm_usage_records，不记录 API Key
        cost_yuan = params.get("cost_yuan")
        if cost_yuan is not None:
            try:
                cost_yuan = float(cost_yuan)
            except (ValueError, TypeError):
                cost_yuan = None
        data: Dict[str, Any] = {
            "input_tokens": params.get("input_tokens", 0),
            "cache_read_tokens": params.get("cache_read_tokens", params.get("cacheReadTokens", 0)),
            "reasoning_tokens": params.get("reasoning_tokens", params.get("reasoningTokens", 0)),
            "output_tokens": params.get("output_tokens", 0),
            "total_tokens": params.get("total_tokens", 0),
            "cost_yuan": cost_yuan,
            "model_name": params.get("model_name", params.get("model", "unknown")),
            "provider": params.get("provider", "unknown"),
        }
        request_id = params.get("provider_request_id") or params.get("request_id")
        if request_id:
            data["provider_request_id"] = str(request_id)
        session_id = params.get("sessionId")
        if session_id:
            data["sessionId"] = str(session_id)
        return RuntimeEvent(event_type="usage_updated", data=data)

    def _map_error(self, params: Dict[str, Any]) -> Optional[RuntimeEvent]:
        """映射 error（不打印完整输入；只保留错误码与消息）。"""
        message = str(params.get("message", "Unknown error"))
        # 防止错误消息里意外带出 Key/Prompt：截断并去除写入痕迹
        if len(message) > 300:
            message = message[:300] + "…"
        return RuntimeEvent(
            event_type="error",
            data={
                "code": params.get("code"),
                "message": message,
            },
        )
