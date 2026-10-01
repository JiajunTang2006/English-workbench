"""Harness 运行适配器 (Headless Mode)

封装与 Harness Headless 进程的交互。每个 run 通过 subprocess 调用
``dsh --profile headless --patch <teachmate-overlay> "<task>"`` 完成一次性执行。

架构：
    WorkBench Backend → HarnessRunAdapter → dsh --profile headless (subprocess)
    → Education Plugin → Loopback Bridge → WorkBench API
    → stdout: final assistant text

Phase 1 prototype: 每次调用启动一个一次性 headless 子进程，通过 stdout
获取最终回答。不维持长连接，不流式输出。Phase 2 将升级为持久进程 +
stdin/stdout JSON-RPC 实现流式事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import RuntimeEvent

logger = logging.getLogger(__name__)

_HARNESS_VERSION = "harness-headless-0.1.0"
_DEFAULT_TIMEOUT = 120.0


@dataclass
class HarnessRunResult:
    """Harness 运行结果。"""
    success: bool
    answer: str = ""
    structured_answer: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    harness_session_id: str = ""
    events: list[RuntimeEvent] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    stop_reason: str = ""
    runtime_kind: str = "harness-headless"
    runtime_version: str = _HARNESS_VERSION
    # U3-04: 每个 Provider 请求的用量记录
    usage_records: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 路径 / 环境解析
# ---------------------------------------------------------------------------

def _resolve_harness_root() -> Path:
    """定位 harness 目录树（优先打包路径，回退开发检出版）。"""
    project_root = Path(__file__).resolve().parents[4]
    packaged = project_root / "teachmate-runtime" / "harness"
    if (packaged / "apps" / "cli" / "lib" / "bin.js").is_file():
        return packaged
    vendor = project_root / "vendor" / "deepseek-harness-upstream"
    if (vendor / "apps" / "cli" / "lib" / "bin.js").is_file():
        return vendor
    return packaged  # 路径不存在会在后续调用时报错


def _resolve_node() -> str:
    """定位 node 二进制。"""
    import shutil
    candidate = os.getenv("NODE_BINARY")
    if candidate and Path(candidate).is_file():
        return candidate
    node = shutil.which("node")
    if node:
        return node
    raise FileNotFoundError("找不到 Node.js；Harness headless 需要 Node.js 运行时")


def _resolve_headless_patch() -> Path:
    """定位 headless+teachmate 的 cordis overlay。"""
    root = _resolve_harness_root()
    return root / "examples" / "teachmate" / "cordis.headless.yml"


class HarnessRunAdapter:
    """与 Harness Headless 进程交互的适配器。

    Phase 1 prototype: 每次调用 ``send_message`` 时启动一个新的一次性
    headless 子进程，通过 stdout 获取最终回答。不维持长连接。
    """

    def __init__(
        self,
        base_url: str = "",
        token: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        # base_url / token 保留用于配置教育工具插件（通过环境变量传递），
        # 不再用于 HTTP 通信。
        self._base_url = base_url or os.getenv(
            "TEACHMATE_BASE_URL", "http://127.0.0.1:8765"
        )
        self._token = token or os.getenv("WORKBENCH_TOKEN", "")
        self._default_timeout = timeout
        self._harness_root = _resolve_harness_root()
        self._node = _resolve_node()
        self._patch = _resolve_headless_patch()
        self._sessions: dict[str, dict[str, Any]] = {}

    @property
    def runtime_kind(self) -> str:
        return "harness-headless"

    @property
    def runtime_version(self) -> str:
        return _HARNESS_VERSION

    async def create_session(
        self,
        cwd: str | None = None,
        agent_preset: str = "teachmate",
    ) -> str:
        """创建一个逻辑会话（headless 模式仅记录元数据，不启动进程）。"""
        session_id = f"headless-{uuid.uuid4().hex[:12]}"
        self._sessions[session_id] = {
            "created_at": time.time(),
            "cwd": cwd,
            "preset": agent_preset,
        }
        logger.info("Headless session created: %s (preset=%s)", session_id, agent_preset)
        return session_id

    async def send_message(
        self,
        session_id: str,
        content: str,
        timeout: float | None = None,
    ) -> HarnessRunResult:
        """发送消息到 headless 进程，等待回复。

        Phase 1: 每次调用启动一个一次性 headless 子进程。
        """
        effective_timeout = timeout or self._default_timeout
        start_time = time.time()

        cli = self._harness_root / "apps" / "cli" / "lib" / "bin.js"
        if not cli.is_file():
            return HarnessRunResult(
                success=False, harness_session_id=session_id,
                error=f"Harness CLI not found at {cli}",
                elapsed_ms=int((time.time() - start_time) * 1000),
                stop_reason="error",
            )

        if not self._patch.is_file():
            return HarnessRunResult(
                success=False, harness_session_id=session_id,
                error=f"Headless patch not found at {self._patch}",
                elapsed_ms=int((time.time() - start_time) * 1000),
                stop_reason="error",
            )

        command = [
            self._node, str(cli),
            "--profile", "headless",
            "--patch", str(self._patch),
            content,
        ]

        env = os.environ.copy()
        env["WORKBENCH_TOKEN"] = self._token
        env.setdefault("TEACHMATE_MODE", "bridge")
        env["TEACHMATE_BASE_URL"] = self._base_url
        env.setdefault("DSH_HOME", str(Path.home() / ".workbench" / "harness"))

        loop = asyncio.get_event_loop()

        def _run_subprocess() -> tuple[int, bytes, bytes]:
            proc = subprocess.Popen(
                command,
                cwd=str(self._harness_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = proc.communicate(timeout=effective_timeout)
                return proc.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                return -1, stdout, stderr

        try:
            returncode, stdout_bytes, stderr_bytes = await loop.run_in_executor(
                None, _run_subprocess
            )
        except asyncio.CancelledError:
            return HarnessRunResult(
                success=False, harness_session_id=session_id,
                error="cancelled",
                elapsed_ms=int((time.time() - start_time) * 1000),
                stop_reason="cancelled",
            )

        elapsed_ms = int((time.time() - start_time) * 1000)
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        if returncode == -1:
            return HarnessRunResult(
                success=False, harness_session_id=session_id,
                error=f"Harness headless timed out after {effective_timeout}s",
                elapsed_ms=elapsed_ms,
                stop_reason="timeout",
            )

        if returncode != 0:
            error_msg = stderr_text.strip() or f"Process exited with code {returncode}"
            logger.error("Harness headless failed (rc=%d): %s", returncode, error_msg[:500])
            return HarnessRunResult(
                success=False, harness_session_id=session_id,
                error=error_msg,
                elapsed_ms=elapsed_ms,
                stop_reason="error",
            )

        # 解析 stdout 获取回答
        answer = self._extract_answer_from_stdout(stdout_text)
        structured = self._try_parse_structured(answer)

        # U3-02: 不再从模型最终文本反向提取证据。
        # 证据由工具执行时创建（EvidenceLedger.add），先于模型结论。

        # 合成最小生命周期事件
        events = [
            RuntimeEvent(
                event_type="run.started",
                data={"session_id": session_id},
                timestamp=start_time,
            ),
            RuntimeEvent(
                event_type="run.completed",
                data={"reason": "completed"},
                timestamp=time.time(),
            ),
        ]

        # U3-04: 从事件中提取用量记录（token-meter/update 事件）
        usage_records = self._extract_usage_records(events)

        return HarnessRunResult(
            success=True, answer=answer,
            structured_answer=structured, evidence=[],
            harness_session_id=session_id, events=events,
            elapsed_ms=elapsed_ms,
            stop_reason="completed",
            usage_records=usage_records,
        )

    async def cancel_session(self, session_id: str) -> bool:
        """取消会话（headless 模式仅清理记录）。"""
        self._sessions.pop(session_id, None)
        logger.info("Headless session cancelled: %s", session_id)
        return True

    async def close_session(self, session_id: str) -> None:
        """关闭/清理 headless 会话记录。"""
        self._sessions.pop(session_id, None)
        logger.info("Headless session closed: %s", session_id)

    def collect_events(self, session_id: str) -> list[RuntimeEvent]:
        """获取已收集的运行时事件（headless 模式返回空列表）。"""
        return []

    async def health_check(self) -> dict[str, Any]:
        """检查 Harness headless 是否可用（验证 CLI 和 patch 文件存在）。"""
        cli = self._harness_root / "apps" / "cli" / "lib" / "bin.js"
        issues = []
        if not cli.is_file():
            issues.append(f"CLI not found: {cli}")
        if not self._patch.is_file():
            issues.append(f"Patch not found: {self._patch}")
        try:
            _resolve_node()
        except FileNotFoundError as e:
            issues.append(str(e))

        if issues:
            return {"status": "unhealthy", "issues": issues}
        return {
            "status": "healthy",
            "runtime": "harness-headless",
            "cli": str(cli),
            "patch": str(self._patch),
        }

    # --- 内部方法 ---

    def _extract_answer_from_stdout(self, stdout_text: str) -> str:
        """从 headless stdout 中提取最终回答文本。

        headless 模式下 stdout 可能包含：
        1. 纯文本回答（最常见）
        2. 带有控制字符/日志前缀的回答
        3. JSON 编码的回答（如果 agent 输出了 JSON）

        策略：去掉常见的日志行（以时间戳或 [level] 开头），取剩余内容。
        """
        lines = stdout_text.strip().split("\n")
        answer_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            # 跳过空行
            if not stripped:
                if answer_lines:
                    answer_lines.append("")
                continue
            # 跳过明显的日志行（时间戳开头或 [level] 格式）
            if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", stripped):
                continue
            if re.match(r"^\[(debug|info|warn|error|trace)\b", stripped, re.IGNORECASE):
                continue
            if re.match(r"^\[[\w-]+\]\s*(debug|info|warn|error)", stripped, re.IGNORECASE):
                continue
            answer_lines.append(line)

        answer = "\n".join(answer_lines).strip()
        if not answer:
            # 如果过滤后为空，返回原始 stdout
            answer = stdout_text.strip()
        return answer

    def _try_parse_structured(self, answer: str) -> dict[str, Any] | None:
        """尝试从回答中解析结构化输出（JSON 块或内嵌 JSON）。"""
        # 尝试找 ```json ... ``` 代码块
        json_block = re.search(r"```(?:json)?\s*\n(\{[\s\S]*?\})\s*\n```", answer)
        if json_block:
            try:
                parsed = json.loads(json_block.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass

        # 尝试整个回答是否就是 JSON
        try:
            parsed = json.loads(answer)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass

        return None

    @staticmethod
    def _extract_usage_records(
        events: list[RuntimeEvent]
    ) -> list[dict[str, Any]]:
        """从运行时事件中提取每个 Provider 请求的用量记录。

        提取 USAGE_UPDATED 事件中的 token 和费用数据。
        每个事件对应一次 Provider 请求。

        返回格式：[{
            "input_tokens": int,
            "output_tokens": int,
            "cost_yuan": float | None,  # None 表示费用未知
            "model_name": str,
            "provider": str,
            "provider_request_id": str | None,
            "stage": str,
        }]

        安全：不记录 API Key，只记录 request id、模型名、Token 和状态。
        """
        from ..event_types import USAGE_UPDATED
        records: list[dict[str, Any]] = []
        for ev in events:
            if ev.event_type not in {USAGE_UPDATED, "usage_updated"}:
                continue
            data = ev.data or {}
            input_tokens = int(data.get("input_tokens", 0))
            cache_read_tokens = int(data.get("cache_read_tokens", 0))
            reasoning_tokens = int(data.get("reasoning_tokens", 0))
            output_tokens = int(data.get("output_tokens", 0))
            # cost_yuan 可能为 None（未知模型定价）
            cost_yuan = data.get("cost_yuan")
            if cost_yuan is not None:
                try:
                    cost_yuan = float(cost_yuan)
                except (ValueError, TypeError):
                    cost_yuan = None
            model_name = data.get("model_name", "unknown")
            provider = data.get("provider", "unknown")
            provider_request_id = data.get("provider_request_id")
            stage = data.get("stage", "text_analysis")
            record = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_yuan": cost_yuan,
                "model_name": model_name,
                "provider": provider,
                "provider_request_id": provider_request_id,
                "stage": stage,
            }
            # 兼容旧事件格式：只有 Provider 实际返回扩展字段时才加入，
            # 避免改变既有 usage_records 的最小安全字段集合。
            if cache_read_tokens or "cache_read_tokens" in data:
                record["cache_read_tokens"] = cache_read_tokens
            if reasoning_tokens or "reasoning_tokens" in data:
                record["reasoning_tokens"] = reasoning_tokens
            if data.get("prompt_tokens") or cache_read_tokens:
                record["provider_prompt_tokens"] = int(data.get("prompt_tokens", 0) or (input_tokens + cache_read_tokens))
            records.append(record)
        return records
