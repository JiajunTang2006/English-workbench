"""H0-3: HarnessRunAdapter (Headless Mode) 测试。

覆盖 headless 适配器的基本行为和 run_executor 的 harness 路径。
Phase 1 prototype: adapter 使用 subprocess 而非 HTTP JSON-RPC。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.agent.runtime.harness_adapter import HarnessRunAdapter, HarnessRunResult
from backend.app.agent.runtime.base import RuntimeEvent
from backend.app.agent.event_types import RUN_STARTED, RUN_COMPLETED, TOOL_COMPLETED, MODEL_DELTA


class TestHarnessRunAdapter:
    """测试 HarnessRunAdapter (Headless Mode) 的基本行为。"""

    def test_runtime_kind_and_version(self):
        adapter = HarnessRunAdapter()
        assert adapter.runtime_kind == "harness-headless"
        assert adapter.runtime_version == "harness-headless-0.1.0"

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        """health_check 应验证 CLI 和 patch 文件存在。"""
        adapter = HarnessRunAdapter()
        # Mock the path checks
        fake_cli = MagicMock()
        fake_cli.is_file.return_value = True
        fake_cli.__str__ = MagicMock(return_value="/fake/cli/bin.js")
        with patch.object(adapter, "_harness_root") as mock_root, \
             patch.object(adapter, "_patch") as mock_patch, \
             patch("backend.app.agent.runtime.harness_adapter._resolve_node", return_value="/usr/bin/node"):
            mock_root.__truediv__ = MagicMock(return_value=fake_cli)
            mock_patch.is_file.return_value = True
            result = await adapter.health_check()
            assert result["status"] == "healthy"
            assert result["runtime"] == "harness-headless"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_missing_cli(self):
        """health_check 应报告缺失的文件。"""
        adapter = HarnessRunAdapter()
        # Use a real non-existent path so is_file() returns False
        adapter._harness_root = Path("/nonexistent/fake/path")
        adapter._patch = Path("/nonexistent/fake/patch.yml")
        with patch("backend.app.agent.runtime.harness_adapter._resolve_node", return_value="/usr/bin/node"):
            result = await adapter.health_check()
            assert result["status"] == "unhealthy"
            assert len(result["issues"]) >= 2

    @pytest.mark.asyncio
    async def test_close_session_cleans_state(self):
        adapter = HarnessRunAdapter()
        adapter._sessions["s1"] = {"created_at": 0}
        await adapter.close_session("s1")
        assert "s1" not in adapter._sessions

    @pytest.mark.asyncio
    async def test_collect_events_returns_empty(self):
        """Headless 模式不维护事件流，collect_events 返回空列表。"""
        adapter = HarnessRunAdapter()
        assert adapter.collect_events("nonexistent") == []

    @pytest.mark.asyncio
    async def test_create_session_returns_id(self):
        """create_session 应返回 headless- 前缀的 session ID。"""
        adapter = HarnessRunAdapter()
        session_id = await adapter.create_session(agent_preset="teachmate")
        assert session_id.startswith("headless-")
        assert session_id in adapter._sessions
        assert adapter._sessions[session_id]["preset"] == "teachmate"

    @pytest.mark.asyncio
    async def test_cancel_session(self):
        """cancel_session 应清理会话记录并返回 True。"""
        adapter = HarnessRunAdapter()
        adapter._sessions["s1"] = {"created_at": 0}
        result = await adapter.cancel_session("s1")
        assert result is True
        assert "s1" not in adapter._sessions

    @pytest.mark.asyncio
    async def test_send_message_missing_cli(self):
        """CLI 文件不存在时应返回失败。"""
        adapter = HarnessRunAdapter()
        # Use a real non-existent path so is_file() returns False
        adapter._harness_root = Path("/nonexistent/fake/path")
        result = await adapter.send_message("s1", "test", timeout=1.0)
        assert result.success is False
        assert "CLI not found" in (result.error or "")
        assert result.stop_reason == "error"

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """send_message 成功时应返回 stdout 中的回答。"""
        adapter = HarnessRunAdapter()

        # Use a real temp dir so path operations work correctly
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "apps" / "cli" / "lib").mkdir(parents=True, exist_ok=True)
        (tmpdir / "apps" / "cli" / "lib" / "bin.js").touch()
        adapter._harness_root = tmpdir
        adapter._patch = tmpdir / "patch.yml"
        adapter._patch.touch()

        # Mock subprocess.Popen
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"Hello World", b"")

        with patch("subprocess.Popen", return_value=mock_proc):
            result = await adapter.send_message("s1", "test", timeout=5.0)

        assert result.success is True
        assert "Hello World" in result.answer
        assert result.stop_reason == "completed"
        assert len(result.events) >= 2  # run.started + run.completed

    @pytest.mark.asyncio
    async def test_send_message_process_failure(self):
        """子进程返回非零退出码时应返回失败。"""
        adapter = HarnessRunAdapter()

        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "apps" / "cli" / "lib").mkdir(parents=True, exist_ok=True)
        (tmpdir / "apps" / "cli" / "lib" / "bin.js").touch()
        adapter._harness_root = tmpdir
        adapter._patch = tmpdir / "patch.yml"
        adapter._patch.touch()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"Error: something went wrong")

        with patch("subprocess.Popen", return_value=mock_proc):
            result = await adapter.send_message("s1", "test", timeout=5.0)

        assert result.success is False
        assert result.stop_reason == "error"
        assert "something went wrong" in (result.error or "")

    @pytest.mark.asyncio
    async def test_send_message_timeout(self):
        """子进程超时应返回 timeout。"""
        import subprocess as sp
        import tempfile
        adapter = HarnessRunAdapter()

        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "apps" / "cli" / "lib").mkdir(parents=True, exist_ok=True)
        (tmpdir / "apps" / "cli" / "lib" / "bin.js").touch()
        adapter._harness_root = tmpdir
        adapter._patch = tmpdir / "patch.yml"
        adapter._patch.touch()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        # After kill(), communicate() returns normally
        mock_proc.communicate.side_effect = [
            sp.TimeoutExpired(cmd=["dsh"], timeout=0.5),
            (b"partial output", b""),
        ]

        with patch("subprocess.Popen", return_value=mock_proc):
            result = await adapter.send_message("s1", "test", timeout=0.5)

        assert result.success is False
        assert result.stop_reason == "timeout"

    def test_extract_answer_from_stdout_filters_logs(self):
        """_extract_answer_from_stdout 应过滤日志行。"""
        adapter = HarnessRunAdapter()
        stdout = """2024-01-01T12:00:00.000Z [info] Starting harness
Hello World
[debug] Processing tool call
This is the answer."""
        answer = adapter._extract_answer_from_stdout(stdout)
        assert "Hello World" in answer
        assert "This is the answer." in answer
        assert "[info]" not in answer
        assert "[debug]" not in answer

    def test_try_parse_structured_json_block(self):
        """_try_parse_structured 应从 ```json 代码块中提取 JSON。"""
        adapter = HarnessRunAdapter()
        text = 'Some text\n```json\n{"status": "draft", "summary": "test"}\n```\nMore text'
        result = adapter._try_parse_structured(text)
        assert result is not None
        assert result["status"] == "draft"
        assert result["summary"] == "test"

    def test_try_parse_structured_plain_json(self):
        """_try_parse_structured 应解析纯 JSON 回答。"""
        adapter = HarnessRunAdapter()
        text = '{"status": "completed", "evidence": []}'
        result = adapter._try_parse_structured(text)
        assert result is not None
        assert result["status"] == "completed"

    def test_try_parse_structured_no_json(self):
        """_try_parse_structured 在非 JSON 回答时返回 None。"""
        adapter = HarnessRunAdapter()
        text = "This is a plain text answer with no JSON."
        result = adapter._try_parse_structured(text)
        assert result is None

    def test_try_extract_evidence_removed(self):
        """U3-02: _try_extract_evidence 方法已被删除，不再从模型文本反向提取证据。"""
        adapter = HarnessRunAdapter()
        assert not hasattr(adapter, "_try_extract_evidence")

    def test_harness_run_result_evidence_empty(self):
        """U3-02: HarnessRunResult.evidence 始终为空列表（证据由工具执行时创建）。"""
        # evidence 字段存在于 HarnessRunResult 但不再从模型文本提取
        from backend.app.agent.runtime.harness_adapter import HarnessRunResult
        result = HarnessRunResult(
            success=True, answer="test", structured_answer=None,
            evidence=[], harness_session_id="s1", events=[],
            elapsed_ms=100, stop_reason="completed",
        )
        assert result.evidence == []


class TestRunExecutorHarnessPath:
    """测试 run_executor 的 harness 路径。"""

    def test_is_harness_mode_default_legacy(self):
        from backend.app.agent.run_executor import _is_harness_mode
        with patch.dict("os.environ", {}, clear=False):
            if "AGENT_RUNTIME" in __import__("os").environ:
                del __import__("os").environ["AGENT_RUNTIME"]
            assert _is_harness_mode() is False

    def test_is_harness_mode_when_set(self):
        from backend.app.agent.run_executor import _is_harness_mode
        with patch.dict("os.environ", {"AGENT_RUNTIME": "harness"}):
            assert _is_harness_mode() is True

    def test_is_harness_mode_case_insensitive(self):
        from backend.app.agent.run_executor import _is_harness_mode
        with patch.dict("os.environ", {"AGENT_RUNTIME": "  HARNESS  "}):
            assert _is_harness_mode() is True