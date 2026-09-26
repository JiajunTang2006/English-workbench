"""P0 统一运行调度与启动恢复生产链测试。"""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import Settings
from backend.app.database import Base
from backend.app.factory import create_app
from backend.app.models.agent_entities import AgentMessage, AgentSession, AnalysisRun
from backend.app.services.agent_runs.scheduler import (
    build_orchestrator_request,
    build_run_scope,
    schedule_analysis_run,
    schedule_recovered_runs,
)


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _make_run(db: Session, *, status: str = "queued", message: str = "分析考试") -> AnalysisRun:
    agent_session = AgentSession(title="P0 测试会话", term_id=1, status="active")
    db.add(agent_session)
    db.flush()
    run = AnalysisRun(
        session_id=agent_session.id,
        capability="exam_analysis",
        term_id=1,
        exam_id=12,
        class_id=3,
        student_id=8,
        status=status,
        input_summary_json={"user_message": message},
    )
    db.add(run)
    db.flush()
    db.add(AgentMessage(
        session_id=agent_session.id,
        analysis_run_id=run.id,
        role="user",
        content_text=message,
    ))
    db.commit()
    db.refresh(run)
    return run


class TestRunScope:
    def test_scope_contains_server_owned_run_id(self, db_factory):
        with db_factory() as db:
            run = _make_run(db)
            request = build_orchestrator_request(run, "分析考试")

        assert request.scope == {
            "run_id": run.id,
            "term_id": 1,
            "exam_id": 12,
            "class_id": 3,
            "student_id": 8,
        }

    def test_report_tool_is_visible_with_real_run_scope(self):
        from backend.app.agent.registry.tools import create_default_registry
        from backend.app.agent.registry.capabilities import create_default_capability_registry

        tool_registry = create_default_registry()
        capability = create_default_capability_registry().get("exam_analysis")
        tools = tool_registry.for_model(
            scope=build_run_scope(MagicMock(
                id=77, term_id=1, exam_id=12, class_id=3, student_id=8,
            )),
            available_tools=capability.required_tools,
        )
        names = {item["function"]["name"] for item in tools}
        assert "submit_teaching_report" in names


@pytest.mark.asyncio
async def test_schedule_analysis_run_passes_run_scope_to_executor(db_factory):
    with db_factory() as db:
        run = _make_run(db)

    registry = MagicMock()
    registry.get_state = AsyncMock(return_value=None)  # B2-05 防重：无活跃调度
    registry.register = AsyncMock()
    registry.start = AsyncMock()
    executor = AsyncMock()
    with patch(
        "backend.app.services.agent_runs.scheduler.get_task_registry",
        return_value=registry,
    ), patch(
        "backend.app.services.agent_runs.scheduler.execute_run",
        new=executor,
    ):
        task = await schedule_analysis_run(run, "分析考试", db_factory)
        await task

    registry.register.assert_awaited_once_with(run.id, run.session_id)
    registry.start.assert_awaited_once()
    assert executor.await_count == 1
    request = executor.await_args.args[2]
    assert request.scope["run_id"] == run.id
    assert request.scope["exam_id"] == 12


@pytest.mark.asyncio
async def test_schedule_waits_for_registry_start_before_execution(db_factory):
    with db_factory() as db:
        run = _make_run(db)

    started = False
    executor_called_before_start = False

    async def mark_started(*_args, **_kwargs):
        nonlocal started
        started = True

    async def execute(*_args, **_kwargs):
        nonlocal executor_called_before_start
        executor_called_before_start = not started

    registry = MagicMock()
    registry.get_state = AsyncMock(return_value=None)  # B2-05 防重：无活跃调度
    registry.register = AsyncMock()
    registry.start = AsyncMock(side_effect=mark_started)
    with patch(
        "backend.app.services.agent_runs.scheduler.get_task_registry",
        return_value=registry,
    ), patch(
        "backend.app.services.agent_runs.scheduler.execute_run",
        new=execute,
    ):
        task = await schedule_analysis_run(run, "分析考试", db_factory)
        await task

    assert executor_called_before_start is False


def test_app_lifespan_schedules_queued_run_from_database(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    factory = app.state.session_factory
    with factory() as db:
        run = _make_run(db, message="lifespan 恢复消息")
        run_id = run.id

    registry = MagicMock()
    registry.get_state = AsyncMock(return_value=None)  # B2-05 防重：无活跃调度
    registry.register = AsyncMock()
    registry.start = AsyncMock()
    executed = threading.Event()
    execute_calls = []

    async def execute(*args, **_kwargs):
        execute_calls.append(args)
        executed.set()

    with patch(
        "backend.app.services.agent_runs.scheduler.get_task_registry",
        return_value=registry,
    ), patch(
        "backend.app.services.agent_runs.scheduler.execute_run",
        new=execute,
    ):
        with TestClient(app):
            assert executed.wait(timeout=2), "lifespan 未执行恢复的 queued run"

    assert registry.register.await_count >= 1
    assert any(call.args[0] == run_id for call in registry.register.await_args_list)
    assert any(
        args[0] == run_id and args[2].scope["run_id"] == run_id
        for args in execute_calls
    )


@pytest.mark.asyncio
async def test_schedule_recovered_runs_rebuilds_message_and_run_scope(db_factory):
    with db_factory() as db:
        run = _make_run(db, message="恢复后继续分析")
        run_id = run.id
        session_id = run.session_id

    registry = MagicMock()
    registry.get_state = AsyncMock(return_value=None)  # B2-05 防重：无活跃调度
    registry.register = AsyncMock()
    registry.start = AsyncMock()
    executor = AsyncMock()
    with patch(
        "backend.app.services.agent_runs.scheduler.get_task_registry",
        return_value=registry,
    ), patch(
        "backend.app.services.agent_runs.scheduler.execute_run",
        new=executor,
    ):
        scheduled = await schedule_recovered_runs(db_factory)
        for task_call in registry.start.await_args_list:
            task = task_call.args[1]
            await task

    assert scheduled == [run_id]
    registry.register.assert_awaited_once_with(run_id, session_id)
    request = executor.await_args.args[2]
    assert request.user_message == "恢复后继续分析"
    assert request.scope["run_id"] == run_id
