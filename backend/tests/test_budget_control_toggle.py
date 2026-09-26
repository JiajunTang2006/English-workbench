"""B3 预算控制开关（默认关闭的可选项）测试。

背景：模型用量仅为理论估算、未经实测。默认不拦截（新模型无需价格条目即可运行、
不做超限确认）；用户可显式开启严格的「未知模型拒绝 + 超限确认」预算门禁。
开启路径：POST /api/v1/agent/budget/switch，或环境变量 WORKBENCH_BUDGET_CONTROL_ENABLED。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.agent.config import AgentConfig
from backend.app.agent.cost import (
    CostEstimator,
    UnknownModelPricingError,
    set_budget_control,
    get_budget_control,
)
from backend.app.models.agent_entities import AgentSession
from backend.app.models.entities import Term, Class, Exam


AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}

TERMINAL = {"completed", "failed", "cancelled", "degraded"}


def _enabled_config() -> AgentConfig:
    base = AgentConfig()
    return replace(
        base,
        feature_flags={**base.feature_flags,
                       "agent_enabled": True, "text_agent_enabled": True},
    )


@pytest.fixture(autouse=True)
def _restore_budget_control():
    """每个测试结束恢复预算控制开关，避免污染其他测试。"""
    yield
    set_budget_control(None)


# ---------------------------------------------------------------------------
# CostEstimator 单元层
# ---------------------------------------------------------------------------

class TestEstimatorToggle:

    def test_default_off_allows_unknown_model(self):
        """默认（未开启）：未知模型按零成本放行、不要求确认。"""
        est = CostEstimator()
        estimate = est.estimate_text("unknown-xyz", 2000, 4096)
        assert estimate.estimated_cost_yuan == 0.0
        assert estimate.requires_confirmation is False

    def test_default_off_actual_cost_zero(self):
        est = CostEstimator()
        assert est.actual_cost("unknown-xyz", 1000, 500) == 0.0

    def test_default_off_check_budget_never_blocks(self):
        est = CostEstimator()
        assert est.check_budget(9999.0) is False

    def test_explicit_on_keeps_strict_behavior(self):
        """显式开启后恢复严格门禁：未知模型拒绝、超限需确认。"""
        est = CostEstimator(budget_control_enabled=True, budget_soft_limit_yuan=0.01)
        with pytest.raises(UnknownModelPricingError):
            est.estimate_text("unknown-xyz", 2000, 4096)
        est2 = CostEstimator(budget_control_enabled=True, budget_soft_limit_yuan=0.01)
        estimate = est2.estimate_text("gpt-4o", 10_000_000, 10_000_000)
        assert estimate.requires_confirmation is True

    def test_env_switch_honored(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_BUDGET_CONTROL_ENABLED", "1")
        assert get_budget_control() is True
        est = CostEstimator()
        with pytest.raises(UnknownModelPricingError):
            est.estimate_text("unknown-xyz", 2000, 4096)

    def test_runtime_switch_overrides_env(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_BUDGET_CONTROL_ENABLED", "0")
        set_budget_control(True)
        assert get_budget_control() is True
        set_budget_control(False)
        assert get_budget_control() is False
        set_budget_control(None)
        assert get_budget_control() is False


# ---------------------------------------------------------------------------
# API 层：status / switch / 发消息门禁
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_BUDGET_CONTROL_ENABLED", raising=False)
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    Session = app.state.session_factory
    with Session() as s:
        term = Term(code="b3", name="预算学期", starts_on=date(2026, 1, 1),
                    ends_on=date(2026, 7, 1), status="active")
        s.add(term)
        s.commit()
        cls = Class(term_id=term.id, name="一班", status="active")
        s.add(cls)
        s.commit()
        exam = Exam(term_id=term.id, name="期中", full_score=100,
                    exam_type="english_total", exam_kind="regular", status="active")
        s.add(exam)
        s.commit()
        sess = AgentSession(title="预算会话", term_id=term.id, exam_id=exam.id,
                            class_id=cls.id, status="active")
        s.add(sess)
        s.commit()
        session_id = sess.id
    with patch("backend.app.routers.agent.get_agent_config",
               return_value=_enabled_config()):
        yield client, session_id


def test_budget_status_reports_default_off(client_ctx):
    client, _sid = client_ctx
    r = client.get("/api/v1/agent/budget/status", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["budget_control_enabled"] is False
    assert body["pricing_models"] >= 1


def test_budget_switch_roundtrip(client_ctx):
    client, _sid = client_ctx
    r = client.post("/api/v1/agent/budget/switch",
                    headers=AUTH_HEADERS, json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["budget_control_enabled"] is True
    assert client.get("/api/v1/agent/budget/status",
                      headers=AUTH_HEADERS).json()["budget_control_enabled"] is True


def test_switch_rejects_non_bool(client_ctx):
    client, _sid = client_ctx
    r = client.post("/api/v1/agent/budget/switch",
                    headers=AUTH_HEADERS, json={"enabled": "yes"})
    assert r.status_code == 422


def test_unknown_model_runs_when_control_off(client_ctx):
    """预算控制关闭（默认）：未知模型可正常发起 run（202），不再 503。"""
    client, session_id = client_ctx
    with patch("backend.app.routers.agent.get_agent_config") as g:
        from backend.app.agent.config import get_agent_config as real_get
        cfg = _enabled_config()
        # 使用未知模型名（任何都行——关闭时不会查价格）
        cfg = replace(cfg, text_model_name="not-in-price-table-xyz")
        g.return_value = cfg
        r = client.post(f"/api/v1/agent/sessions/{session_id}/messages",
                        headers=AUTH_HEADERS, json={"content": "分析期中考试"})
        assert r.status_code == 202, r.text[:200]


def test_unknown_model_rejected_when_control_on(client_ctx):
    """预算控制开启：未知模型发消息返回 503 MODEL_PRICING_MISSING。"""
    client, session_id = client_ctx
    client.post("/api/v1/agent/budget/switch", headers=AUTH_HEADERS,
                json={"enabled": True})
    with patch("backend.app.routers.agent.get_agent_config") as g:
        cfg = _enabled_config()
        cfg = replace(cfg, text_model_name="not-in-price-table-xyz")
        g.return_value = cfg
        r = client.post(f"/api/v1/agent/sessions/{session_id}/messages",
                        headers=AUTH_HEADERS, json={"content": "分析期中考试"})
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "MODEL_PRICING_MISSING"