"""P1-2 并发排队与作用域生命周期（B3 审查修复）。

要求：
- 两个分析同时提交：第二个排队（status=queued）不失败，串行完成后都终态；
- 取消/超时/崩溃后必须清理 scope 文件；
- 进程崩溃残留的 scope 在下一回合被 reset 清除；
- 回合期间 scope 目录恒 ≤1 个活动文件（串行锁语义）。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.agent.orchestrator import OrchestratorRequest
from backend.app.agent.run_executor import _execute_harness_run_managed
from backend.app.agent.task_registry import TaskRegistry
from backend.app.database import Base
from backend.app.models.agent_entities import AgentSession, AnalysisRun
from backend.app.models.entities import Term
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def world(session_factory, tmp_path):
    scopes = tmp_path / "scopes"
    scopes.mkdir(exist_ok=True)
    with session_factory() as db:
        t = Term(code="t1", name="2026春", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        db.add(t)
        db.commit()
        ag = AgentSession(title="S1", term_id=t.id, status="active")
        db.add(ag)
        db.commit()
        r1 = AnalysisRun(session_id=ag.id, term_id=t.id,
                         capability="exam_analysis", status="running")
        r2 = AnalysisRun(session_id=ag.id, term_id=t.id,
                         capability="exam_analysis", status="running")
        db.add_all([r1, r2])
        db.commit()
        return {
            "factory": session_factory, "scopes": str(scopes),
            "term_id": t.id, "agent_id": ag.id,
            "run1": r1.id, "run2": r2.id,
        }


class _GateManager:
    """可编程 fake manager：run1 的 call 阻塞在 event 上，其余立即返回。"""

    def __init__(self, world, gate: asyncio.Event, *, fail: str | None = None):
        class Cfg:
            scope_root = world["scopes"]
        self._config = Cfg()
        self.gate = gate
        self.fail = fail
        self.calls = 0
        self.had_queued_after_block = False
        self.sid_seen: list[str] = []

    async def call(self, method, **kwargs):
        self.calls += 1
        self.sid_seen.append(kwargs.get("sessionId", ""))
        if self.fail:
            raise TimeoutError(self.fail)
        if self.calls == 1:
            await self.gate.wait()  # run1 阻塞，让 run2 排队
        return {"finalResponse": "{\"answer_type\":\"exam_analysis\",\"summary\":\"ok\",\"findings\":[],\"recommendations\":[],\"limitations\":[],\"scope_snapshot\":{}, \"schema_version\":\"1.0.0\"}",
                "finishReason": "stop"}


def _scoped_exec(world, run_id, manager):
    """带 registry 隔离的执行包装（函数内 get_task_registry 已隔离）。"""
    return _execute_harness_run_managed(
        run_id, world["agent_id"], _request_for(world, run_id),
        world["factory"], manager,
    )


def _request_for(world, run_id) -> OrchestratorRequest:
    return OrchestratorRequest(
        teacher_id=1, capability_name="exam_analysis",
        scope={"term_id": world["term_id"], "exam_id": 1},
        user_message=f"分析 run{run_id}", session_id=None,
        db_session_id=world["agent_id"],
    )


@pytest.fixture(autouse=True)
def isolate_registry(monkeypatch):
    """隔离 TaskRegistry：内存语义真实，DB 持久化 noop。"""
    from backend.app.agent import run_executor as re_mod

    fresh = TaskRegistry()

    async def noop_persist(*a, **k):
        return None

    fresh._persist_event = noop_persist
    monkeypatch.setattr(re_mod, "get_task_registry", lambda: fresh)
    return fresh




def _status(world, run_id: int) -> str:
    with world["factory"]() as db:
        return db.get(AnalysisRun, run_id).status


def _scope_files(world) -> list[str]:
    """返回 scope 目录内所有文件（含 .tmp 半成品）。"""
    d = Path(world["scopes"])
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.is_file())


class TestConcurrentQueue:
    def test_second_run_queues_then_completes(self, world):
        gate = asyncio.Event()
        mgr = _GateManager(world, gate)

        async def scenario():
            t1 = asyncio.create_task(_scoped_exec(world, world["run1"], mgr))
            # 等 run1 进入 call 阻塞
            for _ in range(100):
                if mgr.calls >= 1:
                    break
                await asyncio.sleep(0.01)
            t2 = asyncio.create_task(_scoped_exec(world, world["run2"], mgr))
            await asyncio.sleep(0.3)

            # run2 应排队（不直接失败）
            st2 = _status(world, world["run2"])
            assert st2 == "queued", f"run2 应为 queued，实际 {st2}"
            # 锁被 run1 持有，scope 目录只有一个活动文件
            assert len(_scope_files(world)) == 1

            gate.set()
            await asyncio.wait_for(asyncio.gather(t1, t2), timeout=30)
        asyncio.run(scenario())

        assert _status(world, world["run1"]) in ("completed", "degraded", "failed")
        assert _status(world, world["run2"]) in ("completed", "degraded", "failed")
        # 终局：scope 目录零残留
        assert _scope_files(world) == []
        assert mgr.calls == 2
        # 每个 run 使用独立 Harness 会话；跨轮连续性由数据库滚动摘要和
        # 最近消息提供，避免 Harness 工具历史与应用历史重复累积。
        assert len(set(mgr.sid_seen)) == 2

    def test_cancelled_run_releases_lock_and_cleans_scope(self, world):
        gate = asyncio.Event()
        mgr = _GateManager(world, gate)

        async def run():
            t1 = asyncio.create_task(_scoped_exec(world, world["run1"], mgr))
            for _ in range(100):
                if mgr.calls >= 1:
                    break
                await asyncio.sleep(0.01)
            assert len(_scope_files(world)) == 1
            t1.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t1
            # 取消后：scope 清理 + 锁释放（run2 能立即进入）
            assert _scope_files(world) == []
            t2 = asyncio.create_task(_scoped_exec(world, world["run2"], mgr))
            await asyncio.wait_for(t2, timeout=15)
        asyncio.run(run())

        assert _scope_files(world) == []
        assert _status(world, world["run2"]) in ("completed", "degraded", "failed")

    def test_timeout_run_fails_and_cleans_scope(self, world):
        mgr = _GateManager(world, asyncio.Event(), fail="provider timeout")

        async def run():
            await _scoped_exec(world, world["run1"], mgr)
        asyncio.run(run())

        assert _status(world, world["run1"]) in ("failed", "cancelled")
        assert _scope_files(world) == []

    def test_crash_residue_cleaned_on_next_turn(self, world):
        """进程崩溃遗留的 scope 文件在下一个回合被 reset_scopes 清除。"""
        d = Path(world["scopes"])
        (d / "stale-aaaaaaaa.json").write_text(
            json.dumps({"run_id": 99, "term_id": 1}), encoding="utf-8")
        (d / "stale-bbbbbbbb.json").write_text(
            json.dumps({"run_id": 98, "term_id": 1}), encoding="utf-8")
        gate = asyncio.Event()
        gate.set()  # 不阻塞：本测试只验证残留清理
        mgr = _GateManager(world, gate)

        async def run():
            await _scoped_exec(world, world["run1"], mgr)
        asyncio.run(run())

        # 残留全部清除，当前回合 scope 也已清理（终局零残留）
        assert _scope_files(world) == []

    def test_scope_inject_failure_aborts_turn(self, world, monkeypatch):
        """P1-3：scope 注入失败必须终止回合，不得继续请求模型。"""
        import backend.app.agent.run_executor as re_mod

        def _boom(*a, **k):
            raise PermissionError("scope dir unwritable")

        monkeypatch.setattr(
            "backend.app.agent.education_bridge.scope_vault.write_scope", _boom)
        mgr = _GateManager(world, asyncio.Event())

        async def run():
            await _scoped_exec(world, world["run1"], mgr)
        asyncio.run(run())

        assert _status(world, world["run1"]) == "failed"
        with world["factory"]() as db:
            run = db.get(AnalysisRun, world["run1"])
            assert "初始化失败" in (run.error_message or "")
        # 模型从未被调用（fail-closed）
        assert mgr.calls == 0
        assert _scope_files(world) == []
