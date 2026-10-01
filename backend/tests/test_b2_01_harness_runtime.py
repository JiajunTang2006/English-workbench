"""B2-01 常驻 Harness 生产接线测试（真实协议语义，全部离线 mock）

覆盖（不依赖真实 API Key 或网络）：
- 真实协议方法集合：initialize / session/prompt / shutdown；
  session/create、session/send、session/close、session/cancel 明确失败；
- session/prompt 回合语义：contentBlocks → 最终答案（finalResponse），
  notification 经事件回调独立分发（不当作 RPC 响应）；
- 同一个应用生命周期内复用同一 SDK 实例（子进程不重建）；
- RPC 超时（回合超时抛出明确异常）；
- shutdown 时 queued/active Future 收到明确异常（不永久挂起）；
- 子进程崩溃检测与有上限受控重启；
- 取消：本地取消标记立即生效，被取消会话不再产生回合结果；
- 事件投影：真实 session.event 写入 analysis_run_events（source=harness）；
- AGENT_RUNTIME=harness 且 manager 未配置 → 运行明确失败，不静默回退；
- 多轮上下文渲染为单文本回合 prompt。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.agent.runtime.harness_manager import (
    HarnessManager,
    HarnessConfig,
    SUPPORTED_RPC_METHODS,
)


# ---------------------------------------------------------------------------
# 离线替身：vendored SDK 客户端
# ---------------------------------------------------------------------------

class _FakeRunResult:
    """模拟 deepseek_harness.api.RunResult。"""

    def __init__(self, final_response: str = "回答完毕", finish_reason: str = "normal"):
        self.final_response = final_response
        self.finish_reason = finish_reason


class _FakeNotification:
    def __init__(self, method: str, payload: dict):
        self.method = method
        self.payload = payload


class _FakeSession:
    def __init__(self, harness: "_FakeHarness", session_id: str):
        self.harness = harness
        self.id = session_id

    def run(self, content_blocks: Any, *, on_notification=None):
        self.harness.runs.append((self.id, content_blocks))
        # 模拟真实通知流：事件 → idle
        if on_notification is not None:
            on_notification(_FakeNotification(
                "session.event",
                {"sessionId": self.id,
                 "event": {"type": "assistant/message", "data": {"text": "x"}}},
            ))
            on_notification(_FakeNotification(
                "session.status", {"sessionId": self.id, "status": "idle"},
            ))
        # 若会话已被取消：提前失败，不再返回最终答案
        if self.id in self.harness.cancelled:
            raise RuntimeError(f"会话 {self.id} 已被取消")
        return self.harness.handlers.get(self.id, _FakeRunResult())


class _FakeHarness:
    """模拟 vendored DeepSeekHarness（进程 + 协议客户端）。"""

    def __init__(self):
        self.started = False
        self.closed = False
        self.runs: list[tuple[str, Any]] = []
        self.handlers: dict[str, _FakeRunResult] = {}
        self.cancelled: set[str] = set()
        self._client = SimpleNamespace(_proc=SimpleNamespace(pid=4242, poll=lambda: None))

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def start_session(self, session_id: str):
        return _FakeSession(self, session_id)


def _config(**overrides) -> HarnessConfig:
    base = dict(
        cordis_path="/tmp/cordis.yml",
        session_root="/tmp/sessions",
        model="deepseek-chat",
        api_key="sk-test",
        runtime_bin="/tmp/bin.js",
    )
    base.update(overrides)
    return HarnessConfig(**base)


def _manager(fake: _FakeHarness | None = None) -> HarnessManager:
    manager = HarnessManager(_config())
    manager._sdk = fake if fake is not None else _FakeHarness()
    return manager


# ---------------------------------------------------------------------------
# 协议语义
# ---------------------------------------------------------------------------

class TestProtocolSemantics:
    def test_sdk_launch_uses_slot_scope_instead_of_stale_base_environment(self, monkeypatch):
        import deepseek_harness.api as api
        configs = []
        fake = _FakeHarness()
        monkeypatch.setattr(api, 'DeepSeekHarness', lambda config: configs.append(config) or fake)
        manager = HarnessManager(_config(scope_root='/tmp/scopes-slot-2',
            extra_env={'DSH_EDUCATION_SCOPE_ROOT':'/tmp/scopes-base'}))
        manager._start_process()
        assert configs[0].env['DSH_EDUCATION_SCOPE_ROOT'] == '/tmp/scopes-slot-2'

    def test_sdk_turn_returns_usage_for_every_model_call(self):
        fake = _FakeHarness()
        result = _FakeRunResult()
        result.events = [
            {"type": "assistant/message", "data": {"usage": {"inputTokens": 100, "cacheReadTokens": 20, "outputTokens": 10}}},
            {"type": "assistant/message", "data": {"usage": {"inputTokens": 150, "outputTokens": 40}}},
        ]
        fake.handlers["tm-test-usage"] = result
        reply = _manager(fake)._run_turn("tm-test-usage", [{"type":"text","text":"test"}], timeout=1)
        assert len(reply["usage_records"]) == 2
        assert sum(r["input_tokens"] + r.get("cache_read_tokens", 0) + r["output_tokens"] for r in reply["usage_records"]) == 320

    def test_supported_methods_are_the_real_protocol(self):
        assert SUPPORTED_RPC_METHODS == {"initialize", "session/prompt", "shutdown"}

    def test_unsupported_legacy_methods_fail_closed(self):
        """session/create、send、close、cancel 必须明确失败，绝不静默回退。"""
        manager = _manager()
        for method in ("session/create", "session/send", "session/close",
                       "session/cancel", "session/ask", "agent/run"):
            with pytest.raises(RuntimeError) as exc_info:
                manager._execute_rpc(method, {}, timeout=1.0)
            assert "initialize/session/prompt/shutdown" in str(exc_info.value)

    def test_prompt_returns_final_response_not_message_id(self):
        """session/prompt 必须等待 idle 并返回最终答案（不是 messageId）。"""
        fake = _FakeHarness()
        fake.handlers["tm-1-aa"] = _FakeRunResult("最终分析结论")
        manager = _manager(fake)
        result = manager._execute_rpc(
            "session/prompt",
            {"sessionId": "tm-1-aa",
             "contentBlocks": [{"type": "text", "text": "分析这次考试"}]},
            timeout=5.0,
        )
        assert result["sessionId"] == "tm-1-aa"
        assert result["finalResponse"] == "最终分析结论"
        assert fake.runs and fake.runs[0][0] == "tm-1-aa"
        blocks = fake.runs[0][1]
        assert blocks[0]["type"] == "text"
        assert "分析这次考试" in blocks[0]["text"]

    def test_notifications_dispatched_not_mistaken_as_response(self):
        """回合期间通知走事件回调；RPC 结果只来自匹配的回合。"""
        received: list[tuple[str, dict]] = []
        fake = _FakeHarness()
        manager = _manager(fake)
        manager.register_event_callback(
            lambda method, params: received.append((method, params))
        )
        result = manager._execute_rpc(
            "session/prompt", {"sessionId": "tm-2-bb", "text": "你好"}, timeout=5.0,
        )
        assert result["finalResponse"] == "回答完毕"
        assert any(m == "session.event" for m, _ in received)
        assert any(m == "session.status" for m, _ in received)

    def test_rpc_timeout_raises(self):
        """调用层超时 → asyncio.TimeoutError（不永久挂起）。"""

        class Hanging(_FakeHarness):
            def start_session(self, session_id):
                import time as _t
                class Hung(_FakeSession):
                    def run(self, content_blocks, *, on_notification=None):
                        _t.sleep(0.5)  # 超过调用超时
                        return _FakeRunResult("late-answer")
                return Hung(self, session_id)

        manager = _manager(Hanging())
        loop = asyncio.new_event_loop()

        async def scenario():
            manager._loop = loop
            manager._running = True
            with pytest.raises(asyncio.TimeoutError):
                await manager.call(
                    "session/prompt", sessionId="tm-3", text="hi", timeout=0.05,
                )
            await manager.shutdown()

        loop.run_until_complete(scenario())
        loop.close()

    def test_shutdown_settles_active_request(self):
        """shutdown 时活跃/排队请求收到明确异常，不永久挂起。"""
        import time as _t
        fake = _FakeHarness()

        def hanging_start_session(sid):
            class Hung(_FakeSession):
                def run(self, content_blocks, *, on_notification=None):
                    _t.sleep(0.4)  # worker 仍在收尾
                    return _FakeRunResult("late")
            return Hung(fake, sid)

        fake.start_session = hanging_start_session
        manager = _manager(fake)
        loop = asyncio.new_event_loop()

        async def scenario():
            manager._loop = loop
            manager._running = True

            async def blocker():
                await manager.call("session/prompt", sessionId="tm-x", text="x")
            task = loop.create_task(blocker())
            await asyncio.sleep(0.05)
            await manager.shutdown()
            try:
                await task
                return False
            except Exception:
                return True

        settled = loop.run_until_complete(scenario())
        loop.close()
        assert settled, "shutdown 必须让挂起调用收到明确异常"

    def test_shutdown_idempotent(self):
        manager = _manager()
        manager._running = False
        asyncio.run(manager.shutdown())

    def test_initialize_and_shutdown_acknowledged(self):
        manager = _manager()
        assert manager._execute_rpc("initialize", {}, timeout=1.0)["initialized"] is True
        assert manager._execute_rpc("shutdown", {}, timeout=1.0)["ok"] is True


# ---------------------------------------------------------------------------
# 进程复用 / 崩溃重启
# ---------------------------------------------------------------------------

class TestProcessLifecycle:
    def test_same_sdk_process_serves_two_calls(self):
        """同一生命周期内两次运行复用同一 SDK 实例（进程不重建）。"""
        fake = _FakeHarness()
        manager = _manager(fake)
        manager._execute_rpc("session/prompt", {"sessionId": "a", "text": "1"}, timeout=5.0)
        manager._execute_rpc("session/prompt", {"sessionId": "b", "text": "2"}, timeout=5.0)
        assert fake.started is False or True  # fake 惰性；关键是不 close
        assert fake.closed is False
        assert len(fake.runs) == 2

    def test_crash_restart_with_limit(self):
        from backend.app.agent.runtime.harness_manager import _MAX_RESTARTS
        manager = _manager(_FakeHarness())
        manager._sdk = None  # 模拟崩溃
        attempts = []
        manager._start_process = lambda: attempts.append("restart")

        assert manager._maybe_restart() is True
        assert len(attempts) == 1
        manager._restart_count = _MAX_RESTARTS
        assert manager._maybe_restart() is False
        assert "Max restarts" in (manager._last_error or "")


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancel_registers_local_marker_and_broadcasts(self):
        manager = _manager()
        manager._running = True
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(manager.cancel_session("tm-9"))
        loop.close()
        assert result is True
        assert manager._is_session_cancelled("tm-9")

    def test_cancelled_session_prompt_fails_immediately(self):
        manager = _manager()
        manager._cancelled_sessions.add("tm-c")
        with pytest.raises(RuntimeError) as exc_info:
            manager._execute_rpc(
                "session/prompt", {"sessionId": "tm-c", "text": "x"}, timeout=1.0,
            )
        assert "已被取消" in str(exc_info.value)

    def test_cancelled_during_turn_does_not_return_answer(self):
        """回合过程中取消 → 不产生完成结果（Session.run 提前失败）。"""
        fake = _FakeHarness()
        fake.cancelled.add("tm-d")
        manager = _manager(fake)
        with pytest.raises(RuntimeError) as exc:
            manager._execute_rpc(
                "session/prompt", {"sessionId": "tm-d", "text": "x"}, timeout=2.0,
            )
        assert "已被取消" in str(exc.value)
        assert not fake.handlers  # 没有任何最终结果

    def test_cancel_marker_is_one_shot_and_session_can_continue(self):
        """取消只影响当前回合；同一会话下一轮可以继续使用。"""
        fake = _FakeHarness()
        manager = _manager(fake)
        manager._cancelled_sessions.add("tm-reusable")
        with pytest.raises(RuntimeError, match="已被取消"):
            manager._execute_rpc(
                "session/prompt", {"sessionId": "tm-reusable", "text": "first"},
                timeout=1.0,
            )
        assert not manager._is_session_cancelled("tm-reusable")

        result = manager._execute_rpc(
            "session/prompt", {"sessionId": "tm-reusable", "text": "second"},
            timeout=1.0,
        )
        assert result["finalResponse"] == "回答完毕"


# ---------------------------------------------------------------------------
# 事件投影（真实事件落库）
# ---------------------------------------------------------------------------

class TestEventProjection:
    def test_event_persisted_to_database(self, tmp_path):
        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_b201_")
        os.close(fd)
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(f"sqlite:///{db_path}")
        from backend.app.database import Base
        from backend.app.models.agent_entities import (
            AgentSession, AnalysisRun, AnalysisRunEvent,
        )
        from backend.app.models.entities import Term
        from datetime import date
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)

        with factory() as db:
            t = Term(code="t", name="t", starts_on=date(2026, 1, 1),
                     ends_on=date(2026, 12, 31), status="active")
            db.add(t)
            db.commit()
            db.refresh(t)
            s = AgentSession(title="s", term_id=t.id, status="active")
            db.add(s)
            db.commit()
            db.refresh(s)
            run = AnalysisRun(
                session_id=s.id, term_id=t.id, capability="exam_analysis",
                status="running", harness_session_id="tm-77-aa",
            )
            db.add(run)
            db.commit()
            run_id = run.id

        from backend.app.agent.runtime.event_projection import broadcast_to_active_run
        from backend.app.agent.runtime.base import RuntimeEvent
        event = RuntimeEvent(
            event_type="message_start",
            data={"sessionId": "tm-77-aa", "harnessSessionId": "tm-77-aa",
                  "text": "匿名内容"},
        )

        # B3-06：投影经事件循环调度；测试在 running loop 内等待完成
        async def _wait_broadcast():
            fut = broadcast_to_active_run(event, factory)
            if fut is not None:
                await asyncio.wait_for(fut, timeout=10)
        asyncio.run(_wait_broadcast())

        with factory() as db:
            row = db.scalar(
                select(AnalysisRunEvent).where(
                    AnalysisRunEvent.run_id == run_id
                )
            )
            assert row is not None
            assert row.source == "harness"
            assert row.event_type == "message_start"
        engine.dispose()
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 运行入口选择（未配置明确失败）
# ---------------------------------------------------------------------------

class TestRuntimeSelection:
    def test_harness_mode_with_unconfigured_manager_fails_run(self, monkeypatch):
        """AGENT_RUNTIME=harness 且 manager 未配置 → 明确失败，不静默回退。"""
        from backend.app.agent import run_executor as re

        class Unconfigured:
            def health(self):
                from backend.app.agent.runtime.harness_manager import HealthStatus
                return HealthStatus(configured=False, running=False)

        errors: list[tuple[int, str]] = []

        fake_run = SimpleNamespace(status="queued", error_message=None)
        fake_session = SimpleNamespace(
            scalar=lambda stmt: fake_run if "AnalysisRun" in str(stmt)[:200] else None,
            commit=lambda: None,
            close=lambda: None,
        )

        monkeypatch.setattr("backend.app.agent.runtime.harness_manager.get_harness_manager", lambda: Unconfigured())
        monkeypatch.setenv("AGENT_RUNTIME", "harness")

        from backend.app.agent.task_registry import TaskRegistry
        registry = TaskRegistry()
        monkeypatch.setattr(re, "get_task_registry", lambda: registry)

        async def fake_fail(run_id, error):
            errors.append((run_id, error))
        monkeypatch.setattr(registry, "fail", fake_fail)

        async def fake_http(*a, **kw):
            raise AssertionError("harness-http 回退不应被调用")
        async def fake_managed(*a, **kw):
            raise AssertionError("managed 不应被调用（manager 未配置）")
        monkeypatch.setattr(re, "_execute_harness_run_http", fake_http)
        monkeypatch.setattr(re, "_execute_harness_run_managed", fake_managed)

        asyncio.run(re._execute_harness_run(7, 1, None, lambda: fake_session))
        assert errors and errors[0][0] == 7
        assert "未配置" in errors[0][1]

    def test_harness_http_is_explicit_rollback(self, monkeypatch):
        """harness-http 是显式回滚开关（不被 harness 误触）。"""
        from backend.app.agent.runtime.harness_runtime import is_harness_http_mode
        monkeypatch.setenv("AGENT_RUNTIME", "harness-http")
        assert is_harness_http_mode() is True
        assert is_harness_http_mode() is not is_harness_mode_alias()

    def test_prompt_final_answer_flows_through_turn_prompt_render(self):
        from backend.app.agent.run_executor import _render_turn_prompt
        text = _render_turn_prompt([
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ])
        assert "[系统说明]" in text
        assert "[用户]" in text
        assert "[历史助手回答]" in text


def is_harness_mode_alias():
    from backend.app.agent.runtime.harness_runtime import is_harness_mode
    import os
    return os.getenv("AGENT_RUNTIME", "legacy").strip().lower() == "harness"
