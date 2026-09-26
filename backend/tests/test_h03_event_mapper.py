"""H0-3: Harness 事件映射器单元测试。

覆盖各种 Harness session 事件到 RuntimeEvent 的映射规则。
"""

from __future__ import annotations

import pytest

from backend.app.agent.runtime.harness_event_mapper import (
    map_harness_event,
    map_harness_events,
    H_TURN_START,
    H_TURN_END,
    H_STEP_START,
    H_STEP_END,
    H_ASSISTANT_CHUNK,
    H_ASSISTANT_MESSAGE,
    H_TOOL_CALL,
    H_TOOL_RESULT,
    H_AGENT_STATUS,
    H_TOKEN_METER,
)
from backend.app.agent.event_types import (
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


class TestHarnessEventMapper:
    """测试 Harness 事件到 RuntimeEvent 的映射。"""

    def test_turn_start_maps_to_run_started(self):
        raw = {"type": H_TURN_START, "seq": 1, "time": 1692100000000, "data": {"turn": 0}}
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == RUN_STARTED
        assert ev.data["turn"] == 0
        assert ev.timestamp == 1692100000.0

    def test_step_start_maps_to_model_started(self):
        raw = {"type": H_STEP_START, "seq": 2, "time": 1692100001000, "data": {"turn": 0, "step": 0}}
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == MODEL_STARTED
        assert ev.data["step"] == 0

    def test_assistant_chunk_maps_to_model_delta(self):
        raw = {
            "type": H_ASSISTANT_CHUNK, "seq": 3, "time": 1692100002000,
            "data": {"turn": 0, "step": 0, "chunk": {"type": "text-delta", "text": "Hello"}},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == MODEL_DELTA
        assert ev.data["delta"] == "Hello"

    def test_tool_call_maps_to_tool_started(self):
        raw = {
            "type": H_TOOL_CALL, "seq": 4, "time": 1692100003000,
            "data": {"turn": 0, "step": 0, "callId": "call_1", "name": "read_exam_overview"},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == TOOL_STARTED
        assert ev.data["tool_name"] == "read_exam_overview"
        assert ev.data["tool"] == "read_exam_overview"
        assert ev.data["call_id"] == "call_1"

    def test_tool_result_maps_to_tool_completed_with_evidence(self):
        raw = {
            "type": H_TOOL_RESULT, "seq": 5, "time": 1692100004000,
            "data": {
                "turn": 0, "step": 0, "callId": "call_1",
                "message": {"content": [{"type": "text", "text": '{"evidenceId": "ev_1"}'}]},
            },
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == TOOL_COMPLETED
        assert ev.data["evidence_id"] == "ev_1"
        assert ev.data["tool"] == ""
        assert ev.data["is_error"] is False

    def test_tool_result_without_evidence_id(self):
        raw = {
            "type": H_TOOL_RESULT, "seq": 5, "time": 1692100004000,
            "data": {
                "turn": 0, "step": 0, "callId": "call_1",
                "message": {"content": [{"type": "text", "text": "plain text"}]},
            },
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert "evidence_id" not in ev.data

    def test_turn_end_completed_maps_to_run_completed(self):
        raw = {
            "type": H_TURN_END, "seq": 6, "time": 1692100005000,
            "data": {"turn": 0, "reason": {"kind": "completed"}},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == RUN_COMPLETED
        assert ev.data["reason"] == "completed"

    def test_turn_end_error_maps_to_run_failed(self):
        raw = {
            "type": H_TURN_END, "seq": 6, "time": 1692100005000,
            "data": {"turn": 0, "reason": {"kind": "error"}},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == RUN_FAILED

    def test_turn_end_interrupted_maps_to_run_failed(self):
        for reason_kind in ("interrupted", "cancelled"):
            raw = {
                "type": H_TURN_END, "seq": 6, "time": 1692100005000,
                "data": {"turn": 0, "reason": {"kind": reason_kind}},
            }
            ev = map_harness_event(raw)
            assert ev is not None
            assert ev.event_type == RUN_FAILED

    def test_unknown_event_returns_none(self):
        raw = {"type": "session/title", "seq": 7, "time": 1692100006000, "data": {}}
        ev = map_harness_event(raw)
        assert ev is None

    def test_batch_mapping_filters_none(self):
        raws = [
            {"type": H_TURN_START, "seq": 1, "time": 1000, "data": {"turn": 0}},
            {"type": "session/title", "seq": 2, "time": 2000, "data": {}},
            {"type": H_STEP_START, "seq": 3, "time": 3000, "data": {"turn": 0, "step": 0}},
        ]
        events = map_harness_events(raws)
        assert len(events) == 2
        assert events[0].event_type == RUN_STARTED
        assert events[1].event_type == MODEL_STARTED

    def test_chunk_without_text_delta_returns_empty(self):
        raw = {
            "type": H_ASSISTANT_CHUNK, "seq": 3, "time": 1692100002000,
            "data": {"turn": 0, "step": 0, "chunk": {"type": "reasoning", "text": "thinking"}},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.data["delta"] == ""

    def test_timestamp_fallback_when_missing(self):
        raw = {"type": H_TURN_START, "seq": 1, "data": {"turn": 0}}
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.timestamp > 0  # falls back to current time


# ---------------------------------------------------------------------------
# U1-04: 新增事件映射测试
# ---------------------------------------------------------------------------

class TestNewEventMappings:
    """U1-04: 新增的真实 Harness 事件映射。"""

    def test_step_end_maps_to_model_completed(self):
        raw = {
            "type": H_STEP_END, "seq": 4, "time": 1692100003000,
            "data": {"turn": 0, "step": 0},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == MODEL_COMPLETED
        assert ev.data["step"] == 0

    def test_agent_status_idle_maps_to_run_idle(self):
        raw = {
            "type": H_AGENT_STATUS, "seq": 1, "time": 1692100000000,
            "data": {"status": "idle", "session_id": "tm-1-abc"},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == RUN_IDLE
        assert ev.data["session_id"] == "tm-1-abc"

    def test_agent_status_non_idle_returns_none(self):
        raw = {
            "type": H_AGENT_STATUS, "seq": 1, "time": 1692100000000,
            "data": {"status": "thinking"},
        }
        ev = map_harness_event(raw)
        assert ev is None

    def test_assistant_message_maps_to_model_completed(self):
        raw = {
            "type": H_ASSISTANT_MESSAGE, "seq": 5, "time": 1692100004000,
            "data": {"turn": 0, "step": 0, "message": {"content": "分析完成"}},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == MODEL_COMPLETED
        assert ev.data["content"] == "分析完成"

    def test_token_meter_maps_to_usage_updated(self):
        raw = {
            "type": H_TOKEN_METER, "seq": 6, "time": 1692100005000,
            "data": {"meter": {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150}},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == USAGE_UPDATED
        assert ev.data["input_tokens"] == 100
        assert ev.data["output_tokens"] == 50
        assert ev.data["total_tokens"] == 150

    def test_token_meter_flat_data(self):
        """token-meter 数据可能平铺在 data 中。"""
        raw = {
            "type": H_TOKEN_METER, "seq": 6, "time": 1692100005000,
            "data": {"input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
        }
        ev = map_harness_event(raw)
        assert ev is not None
        assert ev.event_type == USAGE_UPDATED
        assert ev.data["input_tokens"] == 200
        assert ev.data["total_tokens"] == 300

    def test_turn_end_degraded_maps_to_run_degraded(self):
        for reason_kind in ("degraded", "partial", "fallback", "truncated"):
            raw = {
                "type": H_TURN_END, "seq": 7, "time": 1692100006000,
                "data": {"turn": 0, "reason": {"kind": reason_kind}},
            }
            ev = map_harness_event(raw)
            assert ev is not None
            assert ev.event_type == RUN_DEGRADED
            assert ev.data["reason"] == reason_kind

    def test_degraded_is_terminal(self):
        """RUN_DEGRADED 应在 TERMINAL_EVENT_TYPES 中。"""
        from backend.app.agent.event_types import TERMINAL_EVENT_TYPES
        assert RUN_DEGRADED in TERMINAL_EVENT_TYPES

    def test_all_new_types_in_all_event_types(self):
        """所有新增类型应在 ALL_EVENT_TYPES 中。"""
        from backend.app.agent.event_types import ALL_EVENT_TYPES
        for t in (RUN_IDLE, RUN_DEGRADED, MODEL_COMPLETED, USAGE_UPDATED):
            assert t in ALL_EVENT_TYPES
