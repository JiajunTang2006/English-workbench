"""LegacyRuntime — 包装现有 AgentOrchestrator

此适配器不改变 AgentOrchestrator 的任何行为，
只将 OrchestratorRequest/Response 映射为统一的 RunRequest/RunResult。

文档 §18.4：
    "legacy.py 只适配现有 AgentOrchestrator，不改变行为"

此文件的存在价值：
1. 建立 AgentRuntime 接口的第一个实现，验证抽象层可行
2. 为 HarnessRuntime 提供等价对比基准
3. 支持 AGENT_RUNTIME=legacy|harness 开关切换
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..orchestrator import AgentOrchestrator, OrchestratorRequest, OrchestratorResponse
from ..config import AgentConfig
from ..event_types import (
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    MODEL_STARTED,
    TOOL_STARTED,
    TOOL_COMPLETED,
)
from .base import (
    AgentRuntime,
    RunRequest,
    RunResult,
    RuntimeEvent,
    CancellationToken,
)

logger = logging.getLogger(__name__)

_LEGACY_VERSION = "legacy-1.0.0"


class LegacyRuntime(AgentRuntime):
    """包装现有 AgentOrchestrator 的运行时适配器。

    所有方法委托给 AgentOrchestrator，不改变任何行为。
    事件回调在关键节点被调用，但 LegacyRuntime 本身不产生 model.delta 等细粒度事件
    （这些需要 Provider 层支持流式输出后才能实现）。
    """

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        config: AgentConfig,
    ) -> None:
        self._orchestrator = orchestrator
        self._config = config
        self._active_runs: dict[str, CancellationToken] = {}

    async def run(self, request: RunRequest) -> RunResult:
        """执行一次 Agent 运行。

        委托给 AgentOrchestrator.run()，映射请求和响应。
        """
        orch_req = request.orchestrator_request
        session_id = orch_req.session_id or ""

        # 注册取消令牌
        token = request.cancellation_token or CancellationToken()
        self._active_runs[session_id] = token

        # 发送 run.started 事件
        self._emit(request.event_sink, RUN_STARTED, {"session_id": session_id})
        self._emit(request.event_sink, MODEL_STARTED, {"session_id": session_id})

        start_time = time.time()

        try:
            # 如果已取消，直接返回
            if token.cancelled:
                self._emit(request.event_sink, RUN_CANCELLED, {"session_id": session_id})
                return RunResult(
                    success=False,
                    answer="",
                    stop_reason="cancelled",
                    error="运行在启动前已被取消",
                    runtime_kind="legacy",
                    runtime_version=_LEGACY_VERSION,
                )

            # 委托给编排器
            orch_response: OrchestratorResponse = await self._orchestrator.run(
                orch_req, db_session=request.db_session
            )

            elapsed_ms = int((time.time() - start_time) * 1000)

            # 映射响应
            result = RunResult(
                success=orch_response.success,
                answer=orch_response.answer,
                structured_answer=orch_response.structured_answer,
                evidence=orch_response.evidence,
                cost_yuan=orch_response.cost_yuan,
                tokens_used=orch_response.tokens_used,
                elapsed_ms=elapsed_ms,
                stop_reason=orch_response.stop_reason,
                error=orch_response.error,
                needs_confirmation=orch_response.needs_confirmation,
                validation=orch_response.validation,
                request_ids=orch_response.request_ids,
                steps=orch_response.steps,
                runtime_kind="legacy",
                runtime_version=_LEGACY_VERSION,
            )

            # 发送终态事件
            if result.success:
                self._emit(request.event_sink, RUN_COMPLETED, {
                    "session_id": session_id,
                    "stop_reason": result.stop_reason,
                })
            elif result.stop_reason == "cancelled":
                self._emit(request.event_sink, RUN_CANCELLED, {
                    "session_id": session_id,
                })
            else:
                self._emit(request.event_sink, RUN_FAILED, {
                    "session_id": session_id,
                    "error": result.error or "未知错误",
                })

            return result

        except asyncio.CancelledError:
            self._emit(request.event_sink, RUN_CANCELLED, {"session_id": session_id})
            return RunResult(
                success=False,
                answer="",
                stop_reason="cancelled",
                error="运行被取消",
                runtime_kind="legacy",
                runtime_version=_LEGACY_VERSION,
            )
        except Exception as e:
            logger.error("LegacyRuntime 运行异常: %s", e, exc_info=True)
            self._emit(request.event_sink, RUN_FAILED, {
                "session_id": session_id,
                "error": str(e),
            })
            return RunResult(
                success=False,
                answer="",
                stop_reason="error",
                error=str(e),
                runtime_kind="legacy",
                runtime_version=_LEGACY_VERSION,
            )
        finally:
            self._active_runs.pop(session_id, None)

    async def cancel(self, run_id: str) -> bool:
        """取消正在运行的运行。

        设置取消令牌，让协作式取消机制生效。
        """
        token = self._active_runs.get(run_id)
        if token is None:
            return False
        token.cancel()
        return True

    async def confirm(self, run_id: str, confirmation: dict) -> RunResult:
        """确认运行（如预算确认）。

        对于 LegacyRuntime，确认逻辑由路由层处理（POST /runs/{id}/confirm），
        此方法主要用于接口完整性。
        """
        # Legacy 的确认流程在路由层通过重新调用 run() 实现
        # 此处返回一个标记需要确认的结果
        return RunResult(
            success=False,
            answer="",
            needs_confirmation=True,
            stop_reason="waiting_confirmation",
            runtime_kind="legacy",
            runtime_version=_LEGACY_VERSION,
        )

    async def close(self) -> None:
        """关闭运行时，委托给编排器的 close()。"""
        await self._orchestrator.close()

    def health(self) -> dict[str, Any]:
        """健康检查。"""
        return {
            "status": "healthy",
            "runtime_kind": "legacy",
            "runtime_version": _LEGACY_VERSION,
            "provider": self._config.text_provider,
            "model": self._config.text_model_name,
            "agent_enabled": self._config.agent_enabled,
        }

    def runtime_version(self) -> str:
        """返回运行时版本标识。"""
        return _LEGACY_VERSION

    def _emit(
        self,
        sink: Any | None,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """发送事件回调（best-effort，不阻塞主流程）。"""
        if sink is None:
            return
        try:
            sink(RuntimeEvent(
                event_type=event_type,
                data=data,
                timestamp=time.time(),
            ))
        except Exception as e:
            logger.warning("事件回调异常: %s", e)
