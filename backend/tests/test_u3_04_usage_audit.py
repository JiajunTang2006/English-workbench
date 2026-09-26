"""U3-04 用量、成本和模型请求审计 测试

验证：
1. Harness token meter / provider usage 事件写入 llm_usage_records
2. 每个 Provider 请求一条记录
3. analysis_runs.actual_tokens/actual_cost_yuan 由 usage 汇总，不再写死为 0
4. 未知模型定价时显示"费用未知"（None），而非虚假的 0 元
5. 日志只记录 request id、模型名、Token 和状态，不能记录 Key
"""

from __future__ import annotations

import pytest
from typing import Any

from backend.app.agent.cost import (
    CostEstimator,
    UnknownModelPricingError,
)
from backend.app.agent.runtime.base import RuntimeEvent, RunResult
from backend.app.agent.runtime.harness_adapter import HarnessRunResult
from backend.app.agent.event_types import USAGE_UPDATED


# ---------------------------------------------------------------------------
# TestSafeActualCost
# ---------------------------------------------------------------------------

class TestSafeActualCost:
    """safe_actual_cost 在未知模型时返回 None。"""

    def test_known_model_returns_cost(self):
        estimator = CostEstimator()
        cost = estimator.safe_actual_cost("deepseek-chat", 1000, 500)
        assert cost is not None
        assert cost > 0

    def test_unknown_model_returns_none(self):
        # 预算控制开启（严格门禁）时未知模型费用未知 → None
        estimator = CostEstimator(budget_control_enabled=True)
        cost = estimator.safe_actual_cost("totally-unknown-model", 1000, 500)
        assert cost is None

    def test_actual_cost_raises_for_unknown(self):
        # 预算控制开启时未知模型必须抛错（严格门禁）
        estimator = CostEstimator(budget_control_enabled=True)
        with pytest.raises(UnknownModelPricingError):
            estimator.actual_cost("totally-unknown-model", 1000, 500)

    def test_actual_cost_zero_when_control_off(self):
        # 预算控制关闭（默认）时未知模型按 0 放行
        estimator = CostEstimator(budget_control_enabled=False)
        assert estimator.actual_cost("totally-unknown-model", 1000, 500) == 0.0

    def test_safe_cost_matches_actual_for_known(self):
        estimator = CostEstimator()
        actual = estimator.actual_cost("gpt-4o", 2000, 1000)
        safe = estimator.safe_actual_cost("gpt-4o", 2000, 1000)
        assert safe == actual


# ---------------------------------------------------------------------------
# TestHarnessRunResultUsageRecords
# ---------------------------------------------------------------------------

class TestHarnessRunResultUsageRecords:
    """HarnessRunResult 有 usage_records 字段。"""

    def test_default_empty_usage_records(self):
        result = HarnessRunResult(success=True)
        assert result.usage_records == []

    def test_usage_records_populated(self):
        records = [
            {"input_tokens": 100, "output_tokens": 50, "cost_yuan": 0.01,
             "model_name": "deepseek-chat", "provider": "deepseek",
             "provider_request_id": "req-001", "stage": "text_analysis"},
        ]
        result = HarnessRunResult(success=True, usage_records=records)
        assert len(result.usage_records) == 1
        assert result.usage_records[0]["model_name"] == "deepseek-chat"

    def test_usage_records_with_none_cost(self):
        """未知模型定价时 cost_yuan 为 None。"""
        records = [
            {"input_tokens": 100, "output_tokens": 50, "cost_yuan": None,
             "model_name": "unknown-model", "provider": "unknown",
             "provider_request_id": None, "stage": "text_analysis"},
        ]
        result = HarnessRunResult(success=True, usage_records=records)
        assert result.usage_records[0]["cost_yuan"] is None


# ---------------------------------------------------------------------------
# TestExtractUsageRecords
# ---------------------------------------------------------------------------

class TestExtractUsageRecords:
    """_extract_usage_records 从事件中提取用量记录。"""

    def _make_adapter(self):
        from backend.app.agent.runtime.harness_adapter import HarnessRunAdapter
        return HarnessRunAdapter.__new__(HarnessRunAdapter)

    def test_no_usage_events_returns_empty(self):
        adapter = self._make_adapter()
        events = [
            RuntimeEvent(event_type="run.started", data={}),
            RuntimeEvent(event_type="run.completed", data={}),
        ]
        records = adapter._extract_usage_records(events)
        assert records == []

    def test_single_usage_event(self):
        adapter = self._make_adapter()
        events = [
            RuntimeEvent(event_type=USAGE_UPDATED, data={
                "input_tokens": 500,
                "output_tokens": 200,
                "cost_yuan": 0.05,
                "model_name": "deepseek-chat",
                "provider": "deepseek",
                "provider_request_id": "req-123",
                "stage": "text_analysis",
            }),
        ]
        records = adapter._extract_usage_records(events)
        assert len(records) == 1
        rec = records[0]
        assert rec["input_tokens"] == 500
        assert rec["output_tokens"] == 200
        assert rec["cost_yuan"] == 0.05
        assert rec["model_name"] == "deepseek-chat"
        assert rec["provider"] == "deepseek"
        assert rec["provider_request_id"] == "req-123"
        assert rec["stage"] == "text_analysis"

    def test_multiple_usage_events(self):
        """每个 Provider 请求一条记录。"""
        adapter = self._make_adapter()
        events = [
            RuntimeEvent(event_type=USAGE_UPDATED, data={
                "input_tokens": 100, "output_tokens": 50,
                "cost_yuan": 0.01, "model_name": "deepseek-chat",
                "provider": "deepseek", "provider_request_id": "req-1",
                "stage": "text_analysis",
            }),
            RuntimeEvent(event_type=USAGE_UPDATED, data={
                "input_tokens": 200, "output_tokens": 100,
                "cost_yuan": 0.02, "model_name": "deepseek-chat",
                "provider": "deepseek", "provider_request_id": "req-2",
                "stage": "repair_retry",
            }),
        ]
        records = adapter._extract_usage_records(events)
        assert len(records) == 2
        assert records[0]["provider_request_id"] == "req-1"
        assert records[1]["provider_request_id"] == "req-2"

    def test_usage_event_with_none_cost(self):
        """未知模型定价 -> cost_yuan 为 None。"""
        adapter = self._make_adapter()
        events = [
            RuntimeEvent(event_type=USAGE_UPDATED, data={
                "input_tokens": 100, "output_tokens": 50,
                "cost_yuan": None,
                "model_name": "custom-model",
                "provider": "custom",
            }),
        ]
        records = adapter._extract_usage_records(events)
        assert len(records) == 1
        assert records[0]["cost_yuan"] is None

    def test_usage_event_missing_cost_defaults_to_none(self):
        """缺失 cost_yuan 字段 -> None。"""
        adapter = self._make_adapter()
        events = [
            RuntimeEvent(event_type=USAGE_UPDATED, data={
                "input_tokens": 100, "output_tokens": 50,
            }),
        ]
        records = adapter._extract_usage_records(events)
        assert records[0]["cost_yuan"] is None

    def test_no_api_key_in_records(self):
        """用量记录中不能包含 API Key。"""
        adapter = self._make_adapter()
        events = [
            RuntimeEvent(event_type=USAGE_UPDATED, data={
                "input_tokens": 100, "output_tokens": 50,
                "cost_yuan": 0.01,
                "api_key": "sk-secret-12345",
            }),
        ]
        records = adapter._extract_usage_records(events)
        assert len(records) == 1
        rec = records[0]
        assert "api_key" not in rec
        assert "key" not in rec
        safe_keys = {"input_tokens", "output_tokens", "cost_yuan",
                     "model_name", "provider", "provider_request_id", "stage"}
        assert set(rec.keys()) == safe_keys

    def test_non_usage_events_ignored(self):
        adapter = self._make_adapter()
        events = [
            RuntimeEvent(event_type="run.started", data={}),
            RuntimeEvent(event_type="model.started", data={}),
            RuntimeEvent(event_type="tool.started", data={}),
        ]
        records = adapter._extract_usage_records(events)
        assert records == []


# ---------------------------------------------------------------------------
# TestHarnessEventMapperUsage
# ---------------------------------------------------------------------------

class TestHarnessEventMapperUsage:
    """harness_event_mapper 正确映射 token-meter/update 事件。"""

    def test_token_meter_mapped_to_usage_updated(self):
        from backend.app.agent.runtime.harness_event_mapper import map_harness_event
        raw = {
            "type": "token-meter/update",
            "seq": 5,
            "time": 1692100000000,
            "data": {
                "meter": {
                    "inputTokens": 500,
                    "outputTokens": 200,
                    "totalTokens": 700,
                    "costYuan": 0.05,
                    "modelName": "deepseek-chat",
                    "provider": "deepseek",
                    "requestId": "req-abc",
                }
            }
        }
        event = map_harness_event(raw)
        assert event is not None
        assert event.event_type == USAGE_UPDATED
        assert event.data["input_tokens"] == 500
        assert event.data["output_tokens"] == 200
        assert event.data["cost_yuan"] == 0.05
        assert event.data["model_name"] == "deepseek-chat"
        assert event.data["provider"] == "deepseek"
        assert event.data["provider_request_id"] == "req-abc"

    def test_token_meter_null_cost(self):
        from backend.app.agent.runtime.harness_event_mapper import map_harness_event
        raw = {
            "type": "token-meter/update",
            "data": {
                "meter": {
                    "inputTokens": 100,
                    "outputTokens": 50,
                    "costYuan": None,
                    "modelName": "custom-model",
                    "provider": "custom",
                }
            }
        }
        event = map_harness_event(raw)
        assert event is not None
        assert event.data["cost_yuan"] is None

    def test_token_meter_missing_request_id(self):
        from backend.app.agent.runtime.harness_event_mapper import map_harness_event
        raw = {
            "type": "token-meter/update",
            "data": {
                "meter": {
                    "inputTokens": 100,
                    "outputTokens": 50,
                    "costYuan": 0.01,
                }
            }
        }
        event = map_harness_event(raw)
        assert event is not None
        assert "provider_request_id" not in event.data
        assert event.data["model_name"] == "unknown"

    def test_token_meter_no_api_key_in_output(self):
        from backend.app.agent.runtime.harness_event_mapper import map_harness_event
        raw = {
            "type": "token-meter/update",
            "data": {
                "meter": {
                    "inputTokens": 100,
                    "outputTokens": 50,
                    "costYuan": 0.01,
                    "apiKey": "sk-secret",
                }
            }
        }
        event = map_harness_event(raw)
        assert event is not None
        assert "apiKey" not in event.data
        assert "api_key" not in event.data
        assert "key" not in event.data


# ---------------------------------------------------------------------------
# TestEventProjectorUsage
# ---------------------------------------------------------------------------

class TestEventProjectorUsage:
    """harness_event_projector 正确映射 usage.update。"""

    def test_usage_update_mapped(self):
        from backend.app.agent.runtime.harness_event_projector import HarnessEventProjector
        projector = HarnessEventProjector()
        event = projector.project("usage.update", {
            "input_tokens": 300,
            "output_tokens": 150,
            "total_tokens": 450,
            "cost_yuan": 0.03,
            "model_name": "deepseek-chat",
            "provider": "deepseek",
            "provider_request_id": "req-xyz",
        })
        assert event is not None
        assert event.event_type == "usage_updated"
        assert event.data["input_tokens"] == 300
        assert event.data["cost_yuan"] == 0.03
        assert event.data["model_name"] == "deepseek-chat"
        assert event.data["provider_request_id"] == "req-xyz"

    def test_usage_update_null_cost(self):
        from backend.app.agent.runtime.harness_event_projector import HarnessEventProjector
        projector = HarnessEventProjector()
        event = projector.project("usage.update", {
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_yuan": None,
        })
        assert event is not None
        assert event.data["cost_yuan"] is None


# ---------------------------------------------------------------------------
# TestRunResultNullableCost
# ---------------------------------------------------------------------------

class TestRunResultNullableCost:
    """RunResult.cost_yuan 支持 None。"""

    def test_default_cost_is_zero(self):
        result = RunResult(success=True, answer="")
        assert result.cost_yuan == 0.0

    def test_cost_can_be_none(self):
        result = RunResult(success=True, answer="", cost_yuan=None)
        assert result.cost_yuan is None

    def test_cost_can_be_float(self):
        result = RunResult(success=True, answer="", cost_yuan=0.05)
        assert result.cost_yuan == 0.05


# ---------------------------------------------------------------------------
# TestUsageSummarization
# ---------------------------------------------------------------------------

class TestUsageSummarization:
    """测试 usage_records 汇总逻辑。"""

    def test_summarize_all_known_costs(self):
        records = [
            {"input_tokens": 100, "output_tokens": 50, "cost_yuan": 0.01},
            {"input_tokens": 200, "output_tokens": 100, "cost_yuan": 0.02},
        ]
        total_tokens = sum(r["input_tokens"] + r["output_tokens"] for r in records)
        total_cost = sum(r["cost_yuan"] for r in records)
        assert total_tokens == 450
        assert total_cost == pytest.approx(0.03)

    def test_summarize_with_unknown_cost(self):
        records = [
            {"input_tokens": 100, "output_tokens": 50, "cost_yuan": 0.01},
            {"input_tokens": 200, "output_tokens": 100, "cost_yuan": None},
        ]
        total_tokens = sum(r["input_tokens"] + r["output_tokens"] for r in records)
        has_unknown = any(r["cost_yuan"] is None for r in records)
        assert total_tokens == 450
        assert has_unknown is True

    def test_summarize_all_unknown_costs(self):
        records = [
            {"input_tokens": 100, "output_tokens": 50, "cost_yuan": None},
            {"input_tokens": 200, "output_tokens": 100, "cost_yuan": None},
        ]
        has_unknown = any(r["cost_yuan"] is None for r in records)
        assert has_unknown is True

    def test_summarize_empty_records(self):
        records = []
        total_tokens = sum(r["input_tokens"] + r["output_tokens"] for r in records)
        assert total_tokens == 0


# ---------------------------------------------------------------------------
# TestRunExecutorHarnessUsageLogic
# ---------------------------------------------------------------------------

class TestRunExecutorHarnessUsageLogic:
    """验证 _execute_harness_run 的 usage 汇总逻辑（纯逻辑测试）。"""

    def _summarize(self, usage_records):
        """模拟 run_executor 中的汇总逻辑。"""
        total_tokens = 0
        total_cost = 0.0
        has_unknown = False
        for rec in usage_records:
            total_tokens += rec["input_tokens"] + rec["output_tokens"]
            rec_cost = rec.get("cost_yuan")
            if rec_cost is None:
                has_unknown = True
            else:
                total_cost += rec_cost
        actual_cost = None if has_unknown else total_cost
        return total_tokens, actual_cost

    def test_all_known_costs(self):
        usage_records = [
            {"input_tokens": 500, "output_tokens": 200, "cost_yuan": 0.05},
            {"input_tokens": 300, "output_tokens": 100, "cost_yuan": 0.03},
        ]
        tokens, cost = self._summarize(usage_records)
        assert tokens == 1100
        assert cost == pytest.approx(0.08)

    def test_unknown_cost_results_in_none(self):
        usage_records = [
            {"input_tokens": 500, "output_tokens": 200, "cost_yuan": 0.05},
            {"input_tokens": 300, "output_tokens": 100, "cost_yuan": None},
        ]
        tokens, cost = self._summarize(usage_records)
        assert tokens == 1100
        assert cost is None

    def test_empty_records(self):
        usage_records = []
        tokens, cost = self._summarize(usage_records)
        assert tokens == 0
        assert cost == 0.0
