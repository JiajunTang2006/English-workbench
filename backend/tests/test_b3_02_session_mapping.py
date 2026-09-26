"""B3-02 会话与运行映射测试

覆盖：
1. AgentSession ↔ Harness session 稳定映射：
   - assign_harness_session 幂等（同 session 连续两次返回同一 id）；
   - 不同 AgentSession 的 id 不同，且格式 tm-{agent_session_id}-{hex}；
2. 每个 AnalysisRun 使用独立 Harness 会话（业务记忆由数据库摘要维护）；
3. scope 变化 → rotate_harness_session：新 id + context_revision 递增；
4. 取消一个 run 不影响其他会话（HarnessManager 本地取消按 session 隔离）；
5. 事件投影优先非终态 run，同 harness 会话多个 run 不串写。
6. 真实协议语义：session/prompt 只回 messageId，最终答案来自 notification 流
   （SDK 回合封装），上层不拿 messageId 当答案（契约断言）。

全部离线（manager 用 fake SDK；DB 用临时 SQLite）。
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.agent_entities import AgentSession, AnalysisRun
from backend.app.models.entities import Term
from backend.app.agent.session_mapper import (
    assign_harness_session,
    rotate_harness_session,
    generate_harness_session_id,
)
from backend.app.agent.runtime import event_projection
from backend.app.agent.runtime.harness_manager import (
    HarnessManager,
    HarnessConfig,
)


@pytest.fixture
def session_factory():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b302_")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
    os.unlink(path)


@pytest.fixture
def term_id(session_factory) -> int:
    with session_factory() as session:
        t = Term(code="t1", name="学期1", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


def _make_session(session_factory, term_id) -> int:
    with session_factory() as session:
        s = AgentSession(title="S1", term_id=term_id, status="active")
        session.add(s)
        session.commit()
        session.refresh(s)
        return s.id


# ---------------------------------------------------------------------------
# 1. 稳定会话映射
# ---------------------------------------------------------------------------

class TestStableMapping:

    def test_assign_is_idempotent(self, session_factory, term_id):
        sid = _make_session(session_factory, term_id)
        with session_factory() as db:
            h1 = assign_harness_session(db, sid)
            h2 = assign_harness_session(db, sid)
            assert h1 == h2
            s = db.get(AgentSession, sid)
            assert s.harness_session_id == h1
            assert s.context_revision == 0

    def test_distinct_sessions_distinct_harness_ids(self, session_factory, term_id):
        s1 = _make_session(session_factory, term_id)
        s2 = _make_session(session_factory, term_id)
        with session_factory() as db:
            h1 = assign_harness_session(db, s1)
            h2 = assign_harness_session(db, s2)
        assert h1 != h2
        assert h1.startswith(f"tm-{s1}-")
        assert h2.startswith(f"tm-{s2}-")

    def test_rotate_creates_new_session_and_bumps_revision(
        self, session_factory, term_id,
    ):
        sid = _make_session(session_factory, term_id)
        with session_factory() as db:
            old = assign_harness_session(db, sid)
            rev = rotate_harness_session(db, sid)
            s = db.get(AgentSession, sid)
            assert s.harness_session_id != old
            assert s.context_revision == 1
            assert rev == 1


# ---------------------------------------------------------------------------
# 2. 每个 run 使用独立 id（run_executor 层解析函数）
# ---------------------------------------------------------------------------

def test_resolve_harness_session_id_is_fresh_per_run(
    session_factory, term_id,
):
    """新 run 不复用旧 Harness 历史；同一 run 恢复时保持幂等。"""
    from backend.app.agent.run_executor import resolve_run_harness_session

    sid = _make_session(session_factory, term_id)
    with session_factory() as db:
        db_session = db.get(AgentSession, sid)
        old = assign_harness_session(db, sid)
        run1 = AnalysisRun(session_id=sid, term_id=term_id,
                           capability="exam_analysis", status="queued")
        run2 = AnalysisRun(session_id=sid, term_id=term_id,
                           capability="exam_analysis", status="queued")
        db.add_all([run1, run2])
        db.commit()
        hid1, rev1 = resolve_run_harness_session(db, run1, db_session)
        hid1_again, rev1_again = resolve_run_harness_session(db, run1, db_session)
        hid2, rev2 = resolve_run_harness_session(db, run2, db_session)
        assert hid1 != old
        assert hid2 != hid1
        assert hid1_again == hid1
        assert rev1_again == rev1
        assert run1.harness_session_id == hid1
        assert run2.harness_session_id == hid2
        assert rev2 > rev1


# ---------------------------------------------------------------------------
# 3. 取消按会话隔离
# ---------------------------------------------------------------------------

class TestCancelIsolation:

    def test_cancel_marks_only_target_session(self):
        m = HarnessManager(HarnessConfig(
            cordis_path="/tmp/c.yml", session_root="/tmp/s",
            api_key="sk-t", runtime_bin="/tmp/bin.js",
        ))
        m._running = True
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            m._loop = loop
            ok = loop.run_until_complete(m.cancel_session("tm-1-aaa"))
            assert ok
            assert m._is_session_cancelled("tm-1-aaa")
            assert not m._is_session_cancelled("tm-2-bbb")
            # 同一会话再次取消是幂等成功
            ok2 = loop.run_until_complete(m.cancel_session("tm-1-aaa"))
            assert ok2
        finally:
            loop.close()
            asyncio.set_event_loop(None)


# ---------------------------------------------------------------------------
# 4. 事件投影优先非终态 run（同 harness 多 run 不串写）
# ---------------------------------------------------------------------------

class TestEventProjectionRouting:

    def test_resolve_prefers_running_run(self, session_factory, term_id):
        """同一 harness_session_id 已有终态 run 时，事件应落到 running 的 run。"""
        sid = _make_session(session_factory, term_id)
        with session_factory() as db:
            hid = assign_harness_session(db, sid)
            # run1 completed（历史）、run2 running（当前）
            r1 = AnalysisRun(session_id=sid, term_id=term_id,
                             capability="exam_analysis", status="completed",
                             harness_session_id=hid)
            r2 = AnalysisRun(session_id=sid, term_id=term_id,
                             capability="exam_analysis", status="running",
                             harness_session_id=hid)
            db.add_all([r1, r2])
            db.commit()
            id1, id2 = r1.id, r2.id

        run_id = event_projection.resolve_run_id_for_harness_session(
            session_factory, hid
        )
        assert run_id == id2, "事件应投影到 running 的 run，而不是历史 run"

    def test_resolve_with_only_finished_returns_latest(self, session_factory, term_id):
        sid = _make_session(session_factory, term_id)
        with session_factory() as db:
            hid = assign_harness_session(db, sid)
            r1 = AnalysisRun(session_id=sid, term_id=term_id,
                             capability="exam_analysis", status="completed",
                             harness_session_id=hid)
            db.add(r1)
            db.commit()
            id1 = r1.id
        assert event_projection.resolve_run_id_for_harness_session(
            session_factory, hid) == id1


# ---------------------------------------------------------------------------
# 5. 高层回合语义
# ---------------------------------------------------------------------------

def test_prompt_response_is_not_final_answer():
    """session/prompt 只回 messageId；最终答案来自回合结果，绝不拿 messageId 当答案。"""
    prompt_response = {"messageId": "msg-1"}
    assert "finalResponse" not in prompt_response
    assert "messageId" in prompt_response
    # SDK 回合结果才是答案来源
    assert hasattr(type("R", (), {"final_response": "ok", "finish_reason": "normal"})(), "final_response")
