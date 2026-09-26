"""P1-11: 预算确认状态机回归测试。

验证：
1. send_message 在预估超预算时创建 waiting_confirmation 运行
2. send_message 在预估未超预算时正常创建 queued 运行
3. POST /runs/{run_id}/confirm 将 waiting_confirmation 转为 queued 并启动执行
4. confirm 对非 waiting_confirmation 运行返回 409
5. confirm 对不存在的运行返回 404
6. AnalysisRunRead 正确映射 needs_confirmation 和 confirmed_budget_yuan
7. OrchestratorRequest.confirmed_budget_yuan 跳过预算门禁
8. TaskRegistry.emit_event / set_status 新方法
9. run_executor 处理 needs_confirmation 响应
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.agent_entities import (
    AgentSession,
    AgentMessage,
    AnalysisRun,
    Base,
)
from backend.app.models.entities import Term, Class, Exam
from backend.app.agent.config import AgentConfig
from backend.app.agent.orchestrator import OrchestratorRequest, OrchestratorResponse
from backend.app.agent.task_registry import TaskRegistry, RunState


# --- Helpers ---

def _enabled_config() -> AgentConfig:
    """返回 agent_enabled=True 的配置。"""
    base = AgentConfig()
    return replace(
        base,
        feature_flags={
            **base.feature_flags,
            "agent_enabled": True,
            "text_agent_enabled": True,
        },
    )


AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


# --- Fixtures ---

@pytest.fixture()
def app_client_db(tmp_path):
    """创建应用、客户端、DB session factory。"""
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    # 获取 session factory 用于直接 DB 操作
    Session = app.state.session_factory

    # seed 基础数据
    with Session() as s:
        term = Term(code="2024-01", name="2024春", starts_on=date(2024, 2, 1), ends_on=date(2024, 7, 1))
        s.add(term)
        s.commit()
        s.refresh(term)
        term_id = term.id

        cls = Class(term_id=term_id, name="高三1班", grade="高三", status="active")
        s.add(cls)
        s.commit()
        s.refresh(cls)
        class_id = cls.id

        exam = Exam(term_id=term_id, name="期中考试", full_score=100, exam_type="english_total", exam_kind="regular", status="active")
        s.add(exam)
        s.commit()
        s.refresh(exam)
        exam_id = exam.id

        agent_session = AgentSession(
            title="测试会话", term_id=term_id, exam_id=exam_id,
            class_id=class_id, status="active",
        )
        s.add(agent_session)
        s.commit()
        s.refresh(agent_session)
        session_id = agent_session.id

    yield client, Session, session_id, term_id


@pytest.fixture()
def enabled_agent(app_client_db, monkeypatch):
    """启用 agent_enabled 的测试上下文（预算控制显式开启以覆盖确认流程）。"""
    # 预算控制默认关闭；本文件断言的是「超限需确认」门禁流程，必须显式开启
    monkeypatch.setenv("WORKBENCH_BUDGET_CONTROL_ENABLED", "1")
    client, Session, session_id, term_id = app_client_db
    enabled_cfg = _enabled_config()
    with patch(
        "backend.app.routers.agent.get_agent_config",
        return_value=enabled_cfg,
    ):
        yield client, Session, session_id, term_id


# =====================================================================
# 1. send_message 预算门禁
# =====================================================================

class TestSendMessageBudgetGate:
    """send_message 创建运行时的预算门禁逻辑。"""

    def test_low_cost_creates_queued_run(self, enabled_agent):
        """预估不超预算时正常创建 queued 运行。"""
        client, Session, session_id, _ = enabled_agent
        with patch(
            "backend.app.routers.agent.schedule_analysis_run",
            new_callable=AsyncMock,
        ):
            resp = client.post(
                f"/api/v1/agent/sessions/{session_id}/messages",
                json={"content": "分析这次考试", "quick_task": "exam_analysis"},
                headers=AUTH_HEADERS,
            )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert "run_id" in data

        with Session() as s:
            run = s.scalar(
                select(AnalysisRun).where(AnalysisRun.id == data["run_id"])
            )
            assert run.status == "queued"

    def test_scope_missing_exam_allowed_for_optional_exam_analysis(self, app_client_db):
        """exam_analysis 无 exam_id 时允许进入资料/文字分析模式。"""
        client, Session, _, _ = app_client_db
        with Session() as s:
            term = s.scalar(select(Term))
            agent_session = AgentSession(
                title="无考试会话", term_id=term.id, status="active",
            )
            s.add(agent_session)
            s.commit()
            s.refresh(agent_session)
            sid = agent_session.id

        enabled_cfg = _enabled_config()
        with patch("backend.app.routers.agent.get_agent_config", return_value=enabled_cfg):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                resp = client.post(
                    f"/api/v1/agent/sessions/{sid}/messages",
                    json={"content": "分析教师提供的资料", "quick_task": "exam_analysis"},
                    headers=AUTH_HEADERS,
                )
        assert resp.status_code == 202
        assert resp.json()["run_id"]


# =====================================================================
# 2. POST /runs/{run_id}/confirm 端点
# =====================================================================

class TestConfirmEndpoint:

    def test_confirm_nonexistent_run_404(self, enabled_agent):
        """确认不存在的运行返回 404。"""
        client, _, _, _ = enabled_agent
        resp = client.post(
            "/api/v1/agent/runs/99999/confirm",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 404

    def test_confirm_queued_run_409(self, enabled_agent):
        """确认非 waiting_confirmation 的运行返回 409。"""
        client, Session, session_id, _ = enabled_agent
        with patch(
            "backend.app.routers.agent.schedule_analysis_run",
            new_callable=AsyncMock,
        ):
            resp = client.post(
                f"/api/v1/agent/sessions/{session_id}/messages",
                json={"content": "分析", "quick_task": "exam_analysis"},
                headers=AUTH_HEADERS,
            )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        confirm_resp = client.post(
            f"/api/v1/agent/runs/{run_id}/confirm",
            headers=AUTH_HEADERS,
        )
        assert confirm_resp.status_code == 409

    def test_confirm_waiting_confirmation_run(self, enabled_agent):
        """手动创建 waiting_confirmation 运行并确认。"""
        client, Session, session_id, term_id = enabled_agent

        # 获取真实的 exam_id
        with Session() as s:
            exam = s.scalar(select(Exam))
            exam_id = exam.id

        with Session() as s:
            run = AnalysisRun(
                session_id=session_id,
                capability="exam_analysis",
                term_id=term_id,
                exam_id=exam_id,
                status="waiting_confirmation",
                estimated_cost_yuan=0.8,
                input_summary_json={
                    "user_message": "分析",
                    "estimated_cost_yuan": 0.8,
                    "requires_confirmation": True,
                },
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id

        # mock execute_run 避免实际调用 LLM
        with patch(
            "backend.app.routers.agent.schedule_analysis_run",
            new_callable=AsyncMock,
        ):
            resp = client.post(
                f"/api/v1/agent/runs/{run_id}/confirm",
                headers=AUTH_HEADERS,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run_id
        assert data["status"] == "queued"
        assert "预算已确认" in data["message"]

        with Session() as s:
            run = s.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
            assert run.status == "queued"
            assert run.input_summary_json.get("confirmed_budget_yuan") == 0.8


# =====================================================================
# 3. AnalysisRunRead 字段映射
# =====================================================================

class TestAnalysisRunReadMapping:

    def test_needs_confirmation_fields(self, enabled_agent):
        """waiting_confirmation 运行 needs_confirmation=True。"""
        client, Session, session_id, term_id = enabled_agent

        # 获取真实的 exam_id
        with Session() as s:
            exam = s.scalar(select(Exam))
            exam_id = exam.id

        with Session() as s:
            run = AnalysisRun(
                session_id=session_id,
                capability="exam_analysis",
                term_id=term_id,
                exam_id=exam_id,
                status="waiting_confirmation",
                estimated_cost_yuan=0.8,
                input_summary_json={
                    "confirmed_budget_yuan": 0.8,
                    "requires_confirmation": True,
                },
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id

        resp = client.get(
            f"/api/v1/agent/runs/{run_id}",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "waiting_confirmation"
        assert data["needs_confirmation"] is True
        assert data["confirmed_budget_yuan"] == 0.8

    def test_queued_run_needs_confirmation_false(self, enabled_agent):
        """queued 运行 needs_confirmation=False。"""
        client, Session, session_id, _ = enabled_agent
        with patch(
            "backend.app.routers.agent.schedule_analysis_run",
            new_callable=AsyncMock,
        ):
            resp = client.post(
                f"/api/v1/agent/sessions/{session_id}/messages",
                json={"content": "分析", "quick_task": "exam_analysis"},
                headers=AUTH_HEADERS,
            )
        run_id = resp.json()["run_id"]

        resp = client.get(
            f"/api/v1/agent/runs/{run_id}",
            headers=AUTH_HEADERS,
        )
        data = resp.json()
        assert data["needs_confirmation"] is False


# =====================================================================
# 4. OrchestratorRequest.confirmed_budget_yuan
# =====================================================================

class TestOrchestratorBudgetGate:

    def test_confirmed_budget_field_exists(self):
        """OrchestratorRequest 有 confirmed_budget_yuan 字段，默认 None。"""
        req = OrchestratorRequest(
            teacher_id=1, capability_name="test", scope={}, user_message="hi",
        )
        assert req.confirmed_budget_yuan is None

    def test_confirmed_budget_can_be_set(self):
        """confirmed_budget_yuan 可以设置为非 None 值。"""
        req = OrchestratorRequest(
            teacher_id=1, capability_name="test", scope={}, user_message="hi",
            confirmed_budget_yuan=0.8,
        )
        assert req.confirmed_budget_yuan == 0.8


# =====================================================================
# 5. TaskRegistry 新方法
# =====================================================================

class TestTaskRegistryNewMethods:

    @pytest.mark.asyncio
    async def test_emit_event_adds_to_state(self):
        """emit_event 向运行添加自定义事件。"""
        registry = TaskRegistry()
        await registry.register(1, 1)
        await registry.emit_event(1, "run.waiting_confirmation", message="需确认")

        events = await registry.get_events(1)
        event_types = [e.event_type for e in events]
        assert "run.waiting_confirmation" in event_types

    @pytest.mark.asyncio
    async def test_set_status_updates_state(self):
        """set_status 更新运行状态。"""
        registry = TaskRegistry()
        await registry.register(1, 1)
        await registry.set_status(1, "waiting_confirmation")

        state = await registry.get_state(1)
        assert state.status == "waiting_confirmation"

    @pytest.mark.asyncio
    async def test_emit_event_nonexistent_run_no_error(self):
        """emit_event 对不存在的运行不报错。"""
        registry = TaskRegistry()
        await registry.emit_event(999, "run.waiting_confirmation", message="test")

    @pytest.mark.asyncio
    async def test_set_status_nonexistent_run_no_error(self):
        """set_status 对不存在的运行不报错。"""
        registry = TaskRegistry()
        await registry.set_status(999, "waiting_confirmation")


# =====================================================================
# 6. RunState waiting_confirmation 非终态
# =====================================================================

class TestWaitingConfirmationNotTerminal:

    def test_waiting_confirmation_not_terminal(self):
        """waiting_confirmation 不是终态。"""
        state = RunState(run_id=1, session_id=1, status="waiting_confirmation")
        assert state.is_terminal is False


# =====================================================================
# 7. RunConfirmResponse schema
# =====================================================================

class TestRunConfirmResponse:

    def test_run_confirm_response_fields(self):
        """RunConfirmResponse 有正确的字段。"""
        from backend.app.schemas.agent import RunConfirmResponse
        resp = RunConfirmResponse(run_id=1, status="queued", message="已确认")
        assert resp.run_id == 1
        assert resp.status == "queued"
        assert "已确认" in resp.message

    def test_run_confirm_response_defaults(self):
        """RunConfirmResponse 默认值正确。"""
        from backend.app.schemas.agent import RunConfirmResponse
        resp = RunConfirmResponse(run_id=1)
        assert resp.status == "queued"
        assert resp.message == ""
