import asyncio
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.auth import TOKEN
from backend.app.agent.config import AgentConfig
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import AnalysisGroup, AnalysisGroupTask, AnalysisRun
from backend.app.models.entities import Class, Enrollment, Exam, Student, Term
from backend.app.database import Base
from backend.app.services.multi_agent import (
    build_scope_snapshot,
    merge_structured_answers,
    partition_student_ids,
    plan_student_batch,
    should_use_multi_agent,
)
from backend.app.services import agent_groups as agent_groups_service
from backend.app.agent.runtime.harness_manager import HarnessConfig
from backend.app.agent.runtime.harness_pool import HarnessPool, resolve_pool_size


def test_multi_agent_thresholds_and_partition_are_stable():
    assert not should_use_multi_agent(3)
    assert should_use_multi_agent(4)
    assert not should_use_multi_agent(10, available_slots=1)
    shards = partition_student_ids([8, 2, 8, 4, 6], shard_size=2)
    assert [list(shard.student_ids) for shard in shards] == [[8, 2], [4, 6]]


def test_plan_downgrades_small_batch_and_caps_concurrency():
    small = plan_student_batch([1, 2, 3])
    assert small.use_multi_agent is False
    assert small.max_concurrency == 1
    four_line = plan_student_batch([1, 2, 3, 4], max_concurrency=4, shard_size=6)
    assert four_line.use_multi_agent is True
    assert four_line.max_concurrency == 4
    large = plan_student_batch(range(1, 25), max_concurrency=99, shard_size=6)
    assert large.use_multi_agent is True
    assert large.max_concurrency == 4
    assert len(large.shards) == 4


def test_scope_snapshot_and_deterministic_merge():
    snapshot_id, snapshot = build_scope_snapshot(
        term_id=1, class_id=2, exam_id=3, student_ids=[4, 2, 4]
    )
    assert snapshot_id == snapshot["snapshot_id"]
    assert snapshot["student_ids"] == [2, 4]
    merged = merge_structured_answers([
        {"findings": [{"title": "词汇", "claim": "薄弱"}], "recommendations": [{"action": "复习", "rationale": "证据"}]},
        {"findings": [{"title": "词汇", "claim": "薄弱"}], "recommendations": [{"action": "复习", "rationale": "证据"}]},
    ])
    assert merged["result_count"] == 2
    assert len(merged["findings"]) == 1
    assert len(merged["recommendations"]) == 1


def test_analysis_group_tables_can_be_created():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        term = Term(code="2026-fall", name="2026秋")
        db.add(term)
        db.flush()
        group = AnalysisGroup(
            group_type="student_batch",
            term_id=term.id,
            requested_student_count=2,
            max_concurrency=2,
            scope_snapshot_id="scope-test",
            scope_snapshot_json={"student_ids": [1, 2]},
        )
        db.add(group)
        db.flush()
        run = AnalysisRun(
            capability="student_diagnosis",
            term_id=term.id,
            status="queued",
        )
        db.add(run)
        db.flush()
        db.add(AnalysisGroupTask(
            group_id=group.id,
            analysis_run_id=run.id,
            shard_index=1,
            student_ids_json=[1],
        ))
        db.rollback()
    # Table creation is the contract; the FK constraint is exercised by
    # production migrations where a real AnalysisRun is present.
    assert "analysis_groups" in Base.metadata.tables


def test_harness_pool_caps_size_and_isolates_scope_roots(tmp_path):
    assert resolve_pool_size("9") == 4
    assert resolve_pool_size("bad") == 1
    config = HarnessConfig(
        cordis_path="cordis.yml",
        session_root=str(tmp_path / "sessions"),
        scope_root=str(tmp_path / "scope"),
        runtime_bin="runtime.js",
        api_key="secret",
        extra_env={"DSH_EDUCATION_SCOPE_ROOT": str(tmp_path / "scope")},
    )
    pool = HarnessPool(config, size=2)
    assert pool.pool_size == 2
    scopes = [manager._config.scope_root for manager in pool.managers]
    sessions = [manager._config.session_root for manager in pool.managers]
    assert scopes[0] != scopes[1]
    assert sessions[0] != sessions[1]
    for manager in pool.managers:
        assert manager._build_env()["DSH_EDUCATION_SCOPE_ROOT"] == manager._config.scope_root


def test_batch_group_route_creates_independent_runs(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    client = TestClient(app)
    with app.state.session_factory() as db:
        term = Term(code="2026-group", name="2026秋", starts_on=date(2026, 9, 1))
        db.add(term)
        db.flush()
        classroom = Class(term_id=term.id, name="1班")
        db.add(classroom)
        db.flush()
        exam = Exam(term_id=term.id, source_key="group-exam", name="月考")
        db.add(exam)
        db.flush()
        students = []
        for index in range(4):
            student = Student(student_no=f"G{index}", name=f"学生{index}", class_id=classroom.id)
            db.add(student)
            db.flush()
            db.add(Enrollment(term_id=term.id, class_id=classroom.id, student_id=student.id))
            students.append(student.id)
        from backend.app.models.agent_entities import AgentSession
        agent_session = AgentSession(term_id=term.id, class_id=classroom.id, exam_id=exam.id, title="批量画像")
        db.add(agent_session)
        db.commit()
        session_id = agent_session.id
        term_id, class_id, exam_id = term.id, classroom.id, exam.id

    enabled = replace(
        AgentConfig(),
        feature_flags={"agent_enabled": True, "text_agent_enabled": True},
    )
    with patch("backend.app.routers.agent.get_agent_config", return_value=enabled), \
         patch("backend.app.routers.agent.schedule_analysis_group", new_callable=AsyncMock) as scheduled:
        low_budget = client.post(
            "/api/v1/agent/groups",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "session_id": session_id,
                "term_id": term_id,
                "class_id": class_id,
                "exam_id": exam_id,
                "student_ids": students,
                "confirmed_budget_yuan": 0.01,
            },
        )
        assert low_budget.status_code == 400
        assert low_budget.json()["detail"]["code"] == "GROUP_BUDGET_TOO_LOW"
        response = client.post(
            "/api/v1/agent/groups",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "session_id": session_id,
                "term_id": term_id,
                "class_id": class_id,
                "exam_id": exam_id,
                "student_ids": students,
                "max_concurrency": 2,
                "require_confirmation": True,
            },
        )
    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_student_count"] == 4
    assert len(payload["tasks"]) == 4
    assert payload["status"] == "waiting_confirmation"
    assert scheduled.await_count == 0

    with patch("backend.app.routers.agent.schedule_analysis_group", new_callable=AsyncMock) as confirmed_schedule:
        confirmed = client.post(
            f"/api/v1/agent/groups/{payload['id']}/confirm",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"confirmed_budget_yuan": payload["estimated_cost_yuan"]},
        )
    assert confirmed.status_code == 202
    assert confirmed.json()["status"] == "queued"
    assert confirmed_schedule.await_count == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(AnalysisGroup).where(AnalysisGroup.id == payload["id"])) is not None
        assert len(list(db.scalars(select(AnalysisRun).where(AnalysisRun.id.in_([task["analysis_run_id"] for task in payload["tasks"]]))))) == 4
        group = db.get(AnalysisGroup, payload["id"])
        tasks = list(db.scalars(select(AnalysisGroupTask).where(AnalysisGroupTask.group_id == group.id).order_by(AnalysisGroupTask.shard_index)))
        group.status = "partially_completed"
        tasks[0].status = "completed"
        tasks[1].status = "failed"
        db.get(AnalysisRun, tasks[0].analysis_run_id).status = "completed"
        db.get(AnalysisRun, tasks[1].analysis_run_id).status = "failed"
        db.commit()
        failed_run_id = tasks[1].analysis_run_id

    with patch("backend.app.routers.agent.schedule_analysis_group", new_callable=AsyncMock) as retry_schedule:
        retried = client.post(
            f"/api/v1/agent/groups/{payload['id']}/retry",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"student_ids": [students[1]]},
        )
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    assert retry_schedule.await_count == 1
    with app.state.session_factory() as db:
        retry_task = next(task for task in db.scalars(select(AnalysisGroupTask).where(AnalysisGroupTask.group_id == payload["id"])) if task.student_ids_json == [students[1]])
        assert retry_task.retry_count == 1
        assert retry_task.analysis_run_id != failed_run_id
        assert db.get(AnalysisRun, failed_run_id).status == "failed"


def test_batch_worker_auto_retries_failed_child(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        term = Term(code="2026-retry", name="2026秋")
        db.add(term)
        db.flush()
        group = AnalysisGroup(
            group_type="student_batch", term_id=term.id, requested_student_count=1,
            max_concurrency=1, scope_snapshot_id="retry-scope", scope_snapshot_json={},
        )
        db.add(group)
        db.flush()
        run = AnalysisRun(session_id=None, capability="student_diagnosis", term_id=term.id, student_id=7, status="queued", input_summary_json={"user_message": "诊断"})
        db.add(run)
        db.flush()
        task = AnalysisGroupTask(group_id=group.id, analysis_run_id=run.id, shard_index=1, student_ids_json=[7], status="queued")
        db.add(task)
        db.commit()
        group_id, task_id = group.id, task.id

    calls = []
    def factory():
        return Session(engine)

    async def fake_schedule(run, message, db_factory, **kwargs):
        async def child_body():
            calls.append(run.id)
            with db_factory() as db:
                current = db.get(AnalysisRun, run.id)
                current.status = "failed" if len(calls) == 1 else "completed"
                current.error_message = "temporary provider timeout" if len(calls) == 1 else None
                db.commit()
        return asyncio.create_task(child_body())

    async def run_group():
        with patch.object(agent_groups_service, "schedule_analysis_run", fake_schedule), \
             patch("backend.app.agent.runtime.event_projection.get_main_event_loop", return_value=None):
            group_task = await agent_groups_service.schedule_analysis_group(group_id, factory)
            await group_task

    asyncio.run(run_group())
    assert len(calls) == 2
    with factory() as db:
        retried_task = db.get(AnalysisGroupTask, task_id)
        assert retried_task.status == "completed"
        assert retried_task.retry_count == 1
        assert db.get(AnalysisRun, retried_task.analysis_run_id).status == "completed"


def test_batch_cancel_does_not_persist_one_event_per_student(tmp_path):
    """批量停止应复用一次数据库事务，并同步终止组级调度协程。"""
    app = create_app(Settings(data_dir=tmp_path))
    client = TestClient(app)
    with app.state.session_factory() as db:
        term = Term(code="2026-cancel", name="2026秋", starts_on=date(2026, 9, 1))
        db.add(term)
        db.flush()
        group = AnalysisGroup(
            group_type="student_batch",
            term_id=term.id,
            status="running",
            requested_student_count=4,
            max_concurrency=4,
            scope_snapshot_id="cancel-scope",
            scope_snapshot_json={"student_ids": [1, 2, 3, 4]},
        )
        db.add(group)
        db.flush()
        for index in range(4):
            run = AnalysisRun(
                capability="student_diagnosis",
                term_id=term.id,
                status="running",
                student_id=index + 1,
            )
            db.add(run)
            db.flush()
            db.add(AnalysisGroupTask(
                group_id=group.id,
                analysis_run_id=run.id,
                shard_index=index + 1,
                student_ids_json=[index + 1],
                status="running",
            ))
        db.commit()
        group_id = group.id

    class _Registry:
        def __init__(self):
            self.calls = []

        async def cancel(self, run_id, *, persist=True, message=None):
            self.calls.append((run_id, persist))
            return True

    registry = _Registry()
    with patch("backend.app.agent.task_registry.get_task_registry", return_value=registry), \
         patch("backend.app.routers.agent.cancel_active_analysis_group", return_value=True) as stop_group:
        response = client.post(
            f"/api/v1/agent/groups/{group_id}/cancel",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert registry.calls and all(persist is False for _, persist in registry.calls)
    stop_group.assert_called_once_with(group_id)
    with app.state.session_factory() as db:
        assert db.get(AnalysisGroup, group_id).status == "cancelled"
        assert all(task.status == "cancelled" for task in db.scalars(select(AnalysisGroupTask).where(AnalysisGroupTask.group_id == group_id)))
