"""HarnessRuntime (EXPERIMENTAL) — 通过 deepseek-harness-sdk 驱动 Harness 运行时

⚠️ 实验性模块 — 不作为生产入口。

架构决策（2026-08）：
    生产路径统一使用 Node Harness（teachmate_runtime.py → examples/teachmate/cordis.yml）。
    此 Python SDK 适配器仅保留为实验性探索和隔离测试用途。
    工程方案 §18 明确不长期采用 SDK 嵌入路线。

实现 AgentRuntime 接口，将请求委托给 DeepSeek Harness SDK。

文档 §18.4：
    "harness.py 通过 HarnessClient 驱动 Harness runtime，
     工具结果进入模型前经过本地 Schema 校验、证据登记和隐私脱敏"

H0-2 阶段：
    - SDK 已安装，Cordis 配置已创建
    - HarnessRuntime 类结构已建立，run() 尝试调用 Harness SDK
    - 运行时二进制尚未构建时，run() fail closed 返回错误结果
    - 支持 health() 检查和 close() 资源释放

H0-3 阶段已完成：
    - 工具桥注入 ✅ (EducationBridge 通过 loopback HTTP 连接 WorkBench API)
    - 事件映射 ✅ (harness_event_mapper.py: turn/step/tool → RuntimeEvent)
    - 运行适配器 ✅ (harness_adapter.py: HarnessRunAdapter 封装 HTTP 交互)
    - 结构化输出和证据提取 ✅ (从 tool/result 提取 evidence_id)
    - run_executor 集成 ✅ (AGENT_RUNTIME=harness 时走 _execute_harness_run)
    - 持久化 runtime_kind/version/harness_session_id ✅
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..event_types import (
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    MODEL_STARTED,
)
from .base import (
    AgentRuntime,
    RunRequest,
    RunResult,
    RuntimeEvent,
    CancellationToken,
)

logger = logging.getLogger(__name__)

_HARNESS_VERSION = "harness-0.1.0"

_CORDIS_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "configs" / "teachmate.cordis.yml"
)


class HarnessRuntime(AgentRuntime):
    """通过 deepseek-harness-sdk 驱动的 Harness 运行时（实验性，非生产入口）。"""

    def __init__(
        self,
        config: AgentConfig,
        cordis_config_path: Path | str | None = None,
    ) -> None:
        self._config = config
        self._cordis_path = Path(cordis_config_path) if cordis_config_path else _CORDIS_CONFIG_PATH
        self._harness: Any = None
        self._active_runs: dict[str, CancellationToken] = {}
        self._initialized = False
        self._init_error: str | None = None

    @property
    def cordis_config_path(self) -> Path:
        return self._cordis_path

    def _ensure_harness(self) -> Any:
        if self._harness is not None:
            return self._harness
        if self._init_error is not None:
            raise RuntimeError(self._init_error)
        try:
            from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

            env: dict[str, str] = {}
            api_key = getattr(self._config, "text_api_key", None)
            if api_key:
                env["DEEPSEEK_API_KEY"] = api_key
            base_url = getattr(self._config, "text_base_url", None)
            if base_url:
                env["DEEPSEEK_BASE_URL"] = base_url

            harness_config = DeepSeekHarnessConfig(
                provider="deepseek-official",
                model=getattr(self._config, "text_model_name", "deepseek-chat"),
                cordis=str(self._cordis_path),
                env=env,
                request_timeout_seconds=120.0,
                shutdown_timeout_seconds=5.0,
            )
            self._harness = DeepSeekHarness(harness_config)
            self._initialized = True
            logger.info("HarnessRuntime initialized, Cordis: %s", self._cordis_path)
            return self._harness
        except ImportError as e:
            self._init_error = f"deepseek-harness-sdk not installed: {e}"
            logger.error(self._init_error)
            raise RuntimeError(self._init_error)
        except Exception as e:
            self._init_error = f"HarnessRuntime init failed: {e}"
            logger.error(self._init_error, exc_info=True)
            raise RuntimeError(self._init_error)

    async def run(self, request: RunRequest) -> RunResult:
        orch_req = request.orchestrator_request
        session_id = orch_req.session_id or str(uuid.uuid4())

        token = request.cancellation_token or CancellationToken()
        self._active_runs[session_id] = token

        self._emit(request.event_sink, RUN_STARTED, {"session_id": session_id})
        self._emit(request.event_sink, MODEL_STARTED, {"session_id": session_id})

        start_time = time.time()

        try:
            if token.cancelled:
                self._emit(request.event_sink, RUN_CANCELLED, {"session_id": session_id})
                return RunResult(
                    success=False, answer="", stop_reason="cancelled",
                    error="cancelled before start",
                    runtime_kind="harness", runtime_version=_HARNESS_VERSION,
                )

            try:
                harness = self._ensure_harness()
            except RuntimeError as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                self._emit(request.event_sink, RUN_FAILED, {
                    "session_id": session_id, "error": str(e),
                })
                return RunResult(
                    success=False, answer="", stop_reason="error",
                    error=f"Harness runtime unavailable: {e}",
                    elapsed_ms=elapsed_ms,
                    runtime_kind="harness", runtime_version=_HARNESS_VERSION,
                )

            loop = asyncio.get_event_loop()

            def _run_harness() -> Any:
                return harness.run(orch_req.user_message, session_id=session_id)

            harness_result = await loop.run_in_executor(None, _run_harness)
            elapsed_ms = int((time.time() - start_time) * 1000)

            finish_reason = getattr(harness_result, "finish_reason", "") or "completed"
            final_response = getattr(harness_result, "final_response", "") or ""

            # Fail-closed: harness 返回 error 或空响应时视为失败
            if finish_reason == "error" or (not final_response and finish_reason != "completed"):
                self._emit(request.event_sink, RUN_FAILED, {
                    "session_id": session_id,
                    "error": f"Harness finish_reason={finish_reason}",
                })
                return RunResult(
                    success=False,
                    answer="",
                    stop_reason="error",
                    error=f"Harness runtime returned finish_reason={finish_reason}",
                    elapsed_ms=elapsed_ms,
                    runtime_kind="harness",
                    runtime_version=_HARNESS_VERSION,
                )

            result = RunResult(
                success=True,
                answer=final_response,
                structured_answer=None,
                evidence=None,
                cost_yuan=0.0,  # 实际费用由 usage_records 汇总
                tokens_used=0,
                elapsed_ms=elapsed_ms,
                stop_reason=finish_reason,
                runtime_kind="harness",
                runtime_version=_HARNESS_VERSION,
            )

            self._emit(request.event_sink, RUN_COMPLETED, {
                "session_id": session_id, "stop_reason": result.stop_reason,
            })
            return result

        except asyncio.CancelledError:
            self._emit(request.event_sink, RUN_CANCELLED, {"session_id": session_id})
            return RunResult(
                success=False, answer="", stop_reason="cancelled",
                error="cancelled", runtime_kind="harness", runtime_version=_HARNESS_VERSION,
            )
        except Exception as e:
            logger.error("HarnessRuntime run error: %s", e, exc_info=True)
            self._emit(request.event_sink, RUN_FAILED, {
                "session_id": session_id, "error": str(e),
            })
            return RunResult(
                success=False, answer="", stop_reason="error",
                error=str(e), runtime_kind="harness", runtime_version=_HARNESS_VERSION,
            )
        finally:
            self._active_runs.pop(session_id, None)

    async def cancel(self, run_id: str) -> bool:
        token = self._active_runs.get(run_id)
        if token is None:
            return False
        token.cancel()

        # 尝试通过 JSON-RPC session/cancel 取消 Harness 端的活跃 turn
        try:
            from backend.app.agent.runtime.harness_manager import get_harness_manager
            manager = get_harness_manager()
            if manager and manager.is_running:
                await manager.cancel_session(run_id)
                logger.info("Harness session/cancel sent for run_id=%s", run_id)
        except Exception as e:
            logger.warning("Failed to send session/cancel to Harness: %s", e)

        return True

    async def confirm(self, run_id: str, confirmation: dict) -> RunResult:
        return RunResult(
            success=False, answer="", needs_confirmation=True,
            stop_reason="waiting_confirmation",
            runtime_kind="harness", runtime_version=_HARNESS_VERSION,
        )

    async def close(self) -> None:
        if self._harness is not None:
            try:
                self._harness.close()
            except Exception as e:
                logger.warning("Harness close error: %s", e)
            finally:
                self._harness = None
                self._initialized = False

    def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._initialized else "uninitialized",
            "runtime_kind": "harness",
            "runtime_version": _HARNESS_VERSION,
            "provider": getattr(self._config, "text_provider", "unknown"),
            "model": getattr(self._config, "text_model_name", "unknown"),
            "cordis_config": str(self._cordis_path),
            "sdk_available": self._check_sdk_available(),
            "runtime_binary_available": self._check_runtime_binary(),
        }

    def runtime_version(self) -> str:
        return _HARNESS_VERSION

    def _check_sdk_available(self) -> bool:
        try:
            import deepseek_harness  # noqa: F401
            return True
        except ImportError:
            return False

    def _check_runtime_binary(self) -> bool:
        try:
            from deepseek_harness_runtime import resolve_bundled_launch_args
            resolve_bundled_launch_args()
            return True
        except Exception:
            return False

    def _emit(
        self,
        sink: Any | None,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        if sink is None:
            return
        try:
            sink(RuntimeEvent(event_type=event_type, data=data, timestamp=time.time()))
        except Exception as e:
            logger.warning("Event sink error: %s", e)
