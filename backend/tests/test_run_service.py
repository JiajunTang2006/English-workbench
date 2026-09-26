"""
test_run_service — U2-01 运行服务测试
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.models.agent_entities import AgentSession, AnalysisRun, AnalysisRunEvent
from backend.app.agent.task_registry import TaskRegistry
from backend.app.services.agent_runs.event_store import EventStore, get_event_store
from backend.app.services.agent_runs.repository import RunRepository
from backend.app.services.agent_runs.service import RunService
from backend.app.agent.event_types import (
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    RUN_DEGRADED,
    RUN_REGISTERED,
    MODEL_DELTA,
    TOOL_STARTED,
    TOOL_COMPLETED,
    USAGE_UPDATED,
    PROGRESS_STEP_STARTED,
    PROGRESS_STEP_COMPLETED,
)


# ---------------------------------------------------------------------------
# EventStore 测试
# ---------------------------------------------------------------------------

class TestEventStore:
    def test_append_and_get(self):
        store = EventStore()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(store.append(1, "test.event", foo="bar"))
            events = loop.run_until_complete(store.get_events(1))
            assert len(events) == 1
            assert events[0].event_type == "test.event"
            assert events[0].data["foo"] == "bar"
            assert events[0].seq == 1
        finally:
            loop.close()

    def test_incremental_get(self):
        store = EventStore()
        loop = asyncio.new_event_loop()
        try:
            for i in range(5):
                loop.run_until_complete(store.append(1, f"event.{i}"))
            # 获取 seq > 2 的事件
            events = loop.run_until_complete(store.get_events(1, after_seq=2))
            assert len(events) == 3
            assert events[0].seq == 3
        finally:
            loop.close()

    def test_clear(self):
        store = EventStore()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(store.append(1, "a"))
            loop.run_until_complete(store.clear(1))
            events = loop.run_until_complete(store.get_events(1))
            assert len(events) == 0
        finally:
            loop.close()

    def test_to_sse(self):
        from backend.app.services.agent_runs.event_store import StoredEvent
        ev = StoredEvent(seq=1, event_type="test", data={"msg": "hello"})
        sse = ev.to_sse()
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")
        assert "test" in sse

    def test_latest_seq(self):
        store = EventStore()
        loop = asyncio.new_event_loop()
        try:
            assert loop.run_until_complete(store.get_latest_seq(1)) == 0
            loop.run_until_complete(store.append(1, "a"))
            loop.run_until_complete(store.append(1, "b"))
            assert loop.run_until_complete(store.get_latest_seq(1)) == 2
        finally:
            loop.close()

    def test_cleanup_terminal(self):
        store = EventStore()
        loop = asyncio.new_event_loop()
        try:
            for i in range(10):
                loop.run_until_complete(store.append(i, "a"))
            loop.run_until_complete(store.cleanup_terminal(max_entries=5))
            # 保留最近 5 个
            assert loop.run_until_complete(store.get_latest_seq(5)) >= 1
            assert loop.run_until_complete(store.get_latest_seq(0)) == 0
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# RunRepository 状态转换测试
# ---------------------------------------------------------------------------

class TestRunRepositoryTransitions:
    def _mock_run(self, status="created"):
        run = MagicMock()
        run.status = status
        run.started_at = None
        run.completed_at = None
        return run

    def test_valid_transition_created_to_validating(self):
        db = MagicMock()
        repo = RunRepository(db)
        run = self._mock_run("created")
        db.scalar.return_value = run
        repo.transition_status(1, "validating")
        assert run.status == "validating"

    def test_valid_transition_running_to_completed(self):
        db = MagicMock()
        repo = RunRepository(db)
        run = self._mock_run("running")
        db.scalar.return_value = run
        repo.transition_status(1, "completed")
        assert run.status == "completed"
        assert run.completed_at is not None

    def test_invalid_transition_created_to_running(self):
        db = MagicMock()
        repo = RunRepository(db)
        run = self._mock_run("created")
        db.scalar.return_value = run
        with pytest.raises(ValueError, match="Invalid status transition"):
            repo.transition_status(1, "running")

    def test_invalid_transition_completed_to_running(self):
        db = MagicMock()
        repo = RunRepository(db)
        run = self._mock_run("completed")
        db.scalar.return_value = run
        with pytest.raises(ValueError, match="Invalid status transition"):
            repo.transition_status(1, "running")

    def test_same_status_noop(self):
        db = MagicMock()
        repo = RunRepository(db)
        run = self._mock_run("running")
        db.scalar.return_value = run
        repo.transition_status(1, "running")
        assert run.status == "running"

    def test_run_not_found(self):
        db = MagicMock()
        repo = RunRepository(db)
        db.scalar.return_value = None
        with pytest.raises(ValueError, match="not found"):
            repo.transition_status(999, "running")

    def test_all_valid_transitions(self):
        """验证完整状态机。"""
        cases = [
            ("created", "validating"),
            ("created", "cancelled"),
            ("created", "failed"),
            ("validating", "estimating"),
            ("validating", "failed"),
            ("estimating", "waiting_confirmation"),
            ("estimating", "queued"),
            ("waiting_confirmation", "queued"),
            ("waiting_confirmation", "cancelled"),
            ("queued", "running"),
            ("running", "validating_output"),
            ("running", "completed"),
            ("running", "degraded"),
            ("running", "failed"),
            ("running", "cancelled"),
            ("running", "waiting_confirmation"),
            ("validating_output", "completed"),
            ("validating_output", "degraded"),
            ("validating_output", "failed"),
        ]
        for old, new in cases:
            db = MagicMock()
            repo = RunRepository(db)
            run = self._mock_run(old)
            db.scalar.return_value = run
            repo.transition_status(1, new)
            assert run.status == new, f"Failed: {old} -> {new}"

    def test_terminal_no_transitions(self):
        """终态不可转出。"""
        for terminal in ("completed", "degraded", "failed", "cancelled"):
            db = MagicMock()
            repo = RunRepository(db)
            run = self._mock_run(terminal)
            db.scalar.return_value = run
            with pytest.raises(ValueError, match="Invalid status transition"):
                repo.transition_status(1, "running")


# ---------------------------------------------------------------------------
# RunService 测试（集成 mock）
# ---------------------------------------------------------------------------

class TestRunService:
    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def event_store(self):
        return EventStore()

    @pytest.fixture
    def service(self, mock_db, event_store):
        return RunService(mock_db, event_store)

    def test_register(self, service, event_store):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(service.register(1, 10))
            events = loop.run_until_complete(event_store.get_events(1))
            assert len(events) >= 1
            assert any(e.event_type == RUN_REGISTERED for e in events)
        finally:
            loop.close()

    def test_emit_model_delta(self, service, event_store):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(service.emit_model_delta(1, "hello"))
            events = loop.run_until_complete(event_store.get_events(1))
            assert len(events) == 1
            assert events[0].event_type == MODEL_DELTA
            assert events[0].data["delta"] == "hello"
        finally:
            loop.close()

    def test_emit_usage(self, service, event_store):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                service.emit_usage(1, 100, 50, 150, cost_yuan=0.5)
            )
            events = loop.run_until_complete(event_store.get_events(1))
            assert events[0].event_type == USAGE_UPDATED
            assert events[0].data["total_tokens"] == 150
        finally:
            loop.close()

    def test_emit_tool_started(self, service, event_store):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                service.emit_tool_started(1, "read_exam", "call_1")
            )
            events = loop.run_until_complete(event_store.get_events(1))
            assert events[0].event_type == TOOL_STARTED
            assert events[0].data["tool_name"] == "read_exam"
        finally:
            loop.close()

    def test_emit_tool_completed_with_evidence(self, service, event_store):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                service.emit_tool_completed(1, "call_1", "ev_1")
            )
            events = loop.run_until_complete(event_store.get_events(1))
            assert events[0].event_type == TOOL_COMPLETED
            assert events[0].data["evidence_id"] == "ev_1"
        finally:
            loop.close()

    def test_emit_progress_public_contract(self, service, event_store):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(service.emit_progress(
                1, "read_data", "running", "正在读取数据",
                summary="读取本次考试成绩", next_action="计算班级表现", order=1,
            ))
            loop.run_until_complete(service.emit_progress(
                1, "read_data", "done", "正在读取数据", order=1,
            ))
            events = loop.run_until_complete(event_store.get_events(1))
            assert events[0].event_type == PROGRESS_STEP_STARTED
            assert events[0].data["next_action"] == "计算班级表现"
            assert events[1].event_type == PROGRESS_STEP_COMPLETED
            assert "prompt" not in events[0].data
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# U2-02: 持久化 & 脱敏测试
# ---------------------------------------------------------------------------

class TestEventSanitization:
    def test_sanitize_removes_api_key(self):
        from backend.app.services.agent_runs.event_store import _sanitize_payload
        data = {"api_key": "sk-12345", "message": "hello"}
        result = _sanitize_payload(data)
        assert result["api_key"] == "[REDACTED]"
        assert result["message"] == "hello"

    def test_sanitize_removes_prompt(self):
        from backend.app.services.agent_runs.event_store import _sanitize_payload
        data = {"prompt": "You are a teacher", "answer": "ok"}
        result = _sanitize_payload(data)
        assert result["prompt"] == "[REDACTED]"
        assert result["answer"] == "ok"

    def test_sanitize_nested(self):
        from backend.app.services.agent_runs.event_store import _sanitize_payload
        data = {"outer": {"api_key": "secret", "safe": "ok"}}
        result = _sanitize_payload(data)
        assert result["outer"]["api_key"] == "[REDACTED]"
        assert result["outer"]["safe"] == "ok"

    def test_sanitize_list_of_dicts(self):
        from backend.app.services.agent_runs.event_store import _sanitize_payload
        data = {"items": [{"token": "abc"}, {"safe": "yes"}]}
        result = _sanitize_payload(data)
        assert result["items"][0]["token"] == "[REDACTED]"
        assert result["items"][1]["safe"] == "yes"

    def test_sanitize_preserves_normal_data(self):
        from backend.app.services.agent_runs.event_store import _sanitize_payload
        data = {"tool_name": "read_exam", "turn": 0, "seq": 5}
        result = _sanitize_payload(data)
        assert result == data


class TestEventStorePersistence:
    def test_persist_event_no_db_is_noop(self):
        """没有 db_session 时 persist_event 是 no-op。"""
        store = EventStore()
        loop = asyncio.new_event_loop()
        try:
            event = store._create_event if hasattr(store, '_create_event') else None
            from backend.app.services.agent_runs.event_store import StoredEvent
            ev = StoredEvent(seq=1, event_type="test", data={"foo": "bar"})
            loop.run_until_complete(store.persist_event(1, ev, db_session=None))
            # 不抛异常即可
        finally:
            loop.close()

    def test_persist_event_with_mock_db(self):
        """用 mock db 测试持久化逻辑。"""
        store = EventStore()
        db = MagicMock()
        loop = asyncio.new_event_loop()
        try:
            from backend.app.services.agent_runs.event_store import StoredEvent
            ev = StoredEvent(seq=1, event_type="test", data={"api_key": "secret"})
            loop.run_until_complete(store.persist_event(1, ev, db_session=db))
            # db.add 应被调用
            assert db.add.called
            db.flush.assert_called_once()
        finally:
            loop.close()

    def test_emit_event_persists_exactly_once(self):
        """RunService 转发到 Registry 时不得重复持久化同一事件。"""
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        db = Session(bind=engine)
        try:
            session = AgentSession(title="唯一事件测试", term_id=1, status="active")
            db.add(session)
            db.flush()
            run = AnalysisRun(
                session_id=session.id,
                capability="exam_analysis",
                term_id=1,
                status="queued",
            )
            db.add(run)
            db.commit()

            service = RunService(db, EventStore())
            service._registry = TaskRegistry()
            asyncio.run(service._registry.register(run.id, session.id, persist=False))
            asyncio.run(service.emit_event(run.id, "tool.started", tool_name="read_exam"))
            db.commit()

            rows = db.scalars(select(AnalysisRunEvent).where(
                AnalysisRunEvent.run_id == run.id,
                AnalysisRunEvent.event_type == "tool.started",
            )).all()
            assert len(rows) == 1
            assert rows[0].seq == 1
        finally:
            db.close()
            engine.dispose()

    def test_append_and_persist_continues_sequence_after_store_restart(self):
        """新 EventStore 实例应从 DB 最大 seq 继续分配，且不重复落库。"""
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        db = Session(bind=engine)
        try:
            session = AgentSession(title="事件测试", term_id=1, status="active")
            db.add(session)
            db.flush()
            run = AnalysisRun(
                session_id=session.id,
                capability="exam_analysis",
                term_id=1,
                status="queued",
            )
            db.add(run)
            db.commit()
            run_id = run.id

            first_store = EventStore()
            asyncio.run(first_store.append_and_persist(
                run_id, "run.started", db_session=db, source="gateway",
            ))
            db.commit()
            assert db.scalar(select(AnalysisRunEvent).where(
                AnalysisRunEvent.run_id == run_id,
                AnalysisRunEvent.seq == 1,
            )) is not None

            second_store = EventStore()
            event = asyncio.run(second_store.append_and_persist(
                run_id, "model.started", db_session=db, source="gateway",
            ))
            db.commit()
            assert event.seq == 2
            rows = db.scalars(
                select(AnalysisRunEvent)
                .where(AnalysisRunEvent.run_id == run_id)
                .order_by(AnalysisRunEvent.seq)
            ).all()
            assert [(row.seq, row.event_type) for row in rows] == [
                (1, "run.started"), (2, "model.started"),
            ]
        finally:
            db.close()
            engine.dispose()

    def test_load_events_from_empty_db(self):
        """从空数据库加载事件返回空列表。"""
        store = EventStore()
        db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        loop = asyncio.new_event_loop()
        try:
            events = loop.run_until_complete(store.load_events_from_db(1, db))
            assert events == []
        finally:
            loop.close()
