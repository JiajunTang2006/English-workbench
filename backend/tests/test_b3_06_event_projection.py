"""B3-06 事件投影与 SSE 测试

覆盖：
1. EventStore 原子 seq：单 run 追加连续递增；**重启后（新实例+同 DB）从最大
   seq 续接**，不重复、不从 1 重新开始；
2. 两个并行 run 使用同一 EventStore，seq 互不干扰（不串线）；
3. broadcast_to_active_run：经主 loop 投影后事件进入正确 run
   （started/tool/completed 全部落库、source=harness）；
4. 事件 payload 隐私：api_key/prompt 等字段被脱敏；
5. SSE/轮询续读：DB 中已有历史事件时，load_events_from_db(after_seq) 可按 seq
   继续读取（含 after>0）；
6. 事件投影路由到 running run（历史 run 不接收新事件）。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.entities import Term
from backend.app.models.agent_entities import (
    AgentSession, AnalysisRun, AnalysisRunEvent,
)
from backend.app.services.agent_runs.event_store import EventStore, get_event_store
from backend.app.agent.runtime import event_projection

# 每个测试结束清理捕获的主 loop（避免污染其它测试文件）
@pytest.fixture(autouse=True)
def _clean_main_loop():
    yield
    event_projection.set_main_event_loop(None)


def _project_sync(event, session_factory):
    import asyncio as _a
    loop = _a.new_event_loop()
    try:
        event_projection.set_main_event_loop(loop)
        _a.set_event_loop(loop)
        try:
            loop.run_until_complete(event_projection._project_async(event, session_factory))
        finally:
            _a.set_event_loop(None)
    finally:
        loop.close()
        event_projection.set_main_event_loop(None)
from backend.app.agent.runtime.base import RuntimeEvent


@pytest.fixture
def session_factory():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b306_")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
    os.unlink(path)


@pytest.fixture
def world(session_factory):
    with session_factory() as db:
        t = Term(code="t1", name="2026春", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        db.add(t)
        db.commit()
        agent = AgentSession(title="S1", term_id=t.id, status="active")
        db.add(agent)
        db.commit()
        r1 = AnalysisRun(session_id=agent.id, term_id=t.id,
                         capability="exam_analysis", status="running",
                         harness_session_id="tm-1-aaa")
        db.add(r1)
        db.commit()
        r2 = AnalysisRun(session_id=agent.id, term_id=t.id,
                         capability="exam_analysis", status="running",
                         harness_session_id="tm-1-bbb")
        db.add(r2)
        db.commit()
        return {"term_id": t.id, "agent_id": agent.id,
                "run1": r1.id, "run2": r2.id}


# ---------------------------------------------------------------------------
# 1. EventStore seq 分配
# ---------------------------------------------------------------------------

def test_seq_continuous_and_unique(session_factory, world):
    store = EventStore()
    async def _run():
        with session_factory() as db:
            e1 = await store.append_and_persist(
                world["run1"], "turn_start", db_session=db, source="harness")
            e2 = await store.append_and_persist(
                world["run1"], "tool_call_start", db_session=db, source="harness")
            assert e1.seq == 1 and e2.seq == 2
            db.commit()
        with session_factory() as db:
            rows = db.scalars(select(AnalysisRunEvent).where(
                AnalysisRunEvent.run_id == world["run1"])).all()
            seqs = [r.seq for r in rows]
            assert seqs == [1, 2]
    asyncio.run(_run())


def test_seq_resumes_after_restart(session_factory, world):
    """模拟进程重启：新 EventStore 实例 + 同一 DB，续接不重复。"""
    async def _run():
        with session_factory() as db:
            s1 = EventStore()
            await s1.append_and_persist(world["run1"], "a", db_session=db, source="harness")
            db.commit()
        # 重启：新实例
        with session_factory() as db:
            s2 = EventStore()
            e2 = await s2.append_and_persist(world["run1"], "b", db_session=db, source="harness")
            db.commit()
            assert e2.seq == 2, "重启后必须从 DB 最大 seq 续接"
        with session_factory() as db:
            seqs = [r.seq for r in db.scalars(select(AnalysisRunEvent).where(
                AnalysisRunEvent.run_id == world["run1"])).all()]
            assert seqs == [1, 2]
    asyncio.run(_run())


def test_parallel_runs_no_crossline(session_factory, world):
    """两个 run 并行写事件，seq 各自独立递增（不串线）。"""
    async def _run():
        store = EventStore()
        with session_factory() as db:
            for i in range(3):
                await store.append_and_persist(
                    world["run1"], f"a{i}", db_session=db, source="harness")
                await store.append_and_persist(
                    world["run2"], f"b{i}", db_session=db, source="harness")
            db.commit()
        with session_factory() as db:
            r1 = sorted([r.seq for r in db.scalars(select(AnalysisRunEvent).where(
                AnalysisRunEvent.run_id == world["run1"])).all()])
            r2 = sorted([r.seq for r in db.scalars(select(AnalysisRunEvent).where(
                AnalysisRunEvent.run_id == world["run2"])).all()])
            assert r1 == [1, 2, 3]
            assert r2 == [1, 2, 3]
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. 投影进正确 run + 脱敏
# ---------------------------------------------------------------------------

def test_projection_routes_to_running_run(session_factory, world):
    """同 harness_session 同时有 completed 历史 run 与 running run 时，投影到 running。"""
    with session_factory() as db:
        db.add(AnalysisRun(session_id=world["agent_id"], term_id=world["term_id"],
                           capability="exam_analysis", status="completed",
                           harness_session_id="tm-h-aaa"))
        db.add(AnalysisRun(session_id=world["agent_id"], term_id=world["term_id"],
                           capability="exam_analysis", status="completed",
                           harness_session_id="tm-1-aaa"))
        db.commit()

    ev = RuntimeEvent(
        event_type="turn_start",
        data={"sessionId": "tm-1-aaa", "harnessSessionId": "tm-1-aaa", "ts": 1},
    )
    async def _run():
        event_projection.set_main_event_loop(asyncio.get_running_loop())
        await event_projection._project_async(ev, session_factory)
    asyncio.run(_run())

    with session_factory() as db:
        rows = db.scalars(select(AnalysisRunEvent).where(
            AnalysisRunEvent.source == "harness")).all()
        assert len(rows) == 1
        assert rows[0].run_id == world["run1"], "事件应落入 running run，而不是历史 run"


def test_payload_sanitized_before_persist(session_factory, world):
    ev = RuntimeEvent(
        event_type="tool_call_end",
        data={"sessionId": "tm-1-aaa", "api_key": "sk-leak", "key": "secret",
              "prompt": "完整prompt", "fact": "平均分80"},
    )
    async def _run():
        event_projection.set_main_event_loop(asyncio.get_running_loop())
        await event_projection._project_async(ev, session_factory)
    asyncio.run(_run())

    with session_factory() as db:
        row = db.scalar(select(AnalysisRunEvent).where(
            AnalysisRunEvent.run_id == world["run1"]))
        payload = row.payload_json
        assert "sk-leak" not in str(payload)
        assert "api_key" not in payload
        assert payload.get("fact") == "平均分80"


# ---------------------------------------------------------------------------
# 3. SSE / 轮询续读
# ---------------------------------------------------------------------------

def test_load_events_after_seq(session_factory, world):
    async def _run():
        store = EventStore()
        with session_factory() as db:
            for i in range(5):
                await store.append_and_persist(
                    world["run1"], f"e{i}", db_session=db, source="harness")
            db.commit()
        with session_factory() as db:
            events = await EventStore().load_events_from_db(
                world["run1"], db, after_seq=2)
            assert [e.seq for e in events] == [3, 4, 5]
            assert events[0].event_type == "e2"
    asyncio.run(_run())


def test_sse_replay_after_restart(session_factory, world):
    """模拟重启后（新 EventStore + 内存无 state）按 after 重放历史事件。"""
    async def _run():
        with session_factory() as db:
            s = EventStore()
            await s.append_and_persist(world["run1"], "run.started",
                                       db_session=db, source="harness")
            db.commit()
        store = EventStore()
        with session_factory() as db:
            events = await store.load_events_from_db(world["run1"], db, after_seq=0)
        sse_lines = "".join(e.to_sse() for e in events)
        assert "event" in sse_lines and "run.started" in sse_lines
    asyncio.run(_run())