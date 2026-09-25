"""AnalysisRun scheduling helpers.

All run entry points use this module so scope construction, task registration,
and restart recovery stay consistent.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ...agent.orchestrator import OrchestratorRequest
from ...agent.run_executor import execute_run
from ...agent.task_registry import get_task_registry
from ...models.agent_entities import AgentMessage, AnalysisRun
from ...services.agent_runs.repository import RunRepository

logger = logging.getLogger(__name__)


def build_run_scope(run: AnalysisRun) -> dict[str, int]:
    """Build the server-owned scope for a persisted run."""
    scope: dict[str, int] = {"run_id": run.id}
    if run.term_id:
        scope["term_id"] = run.term_id
    if run.exam_id:
        scope["exam_id"] = run.exam_id
    if run.class_id:
        scope["class_id"] = run.class_id
    if run.student_id:
        scope["student_id"] = run.student_id
    return scope


def get_run_user_message(db_session, run: AnalysisRun) -> str:
    """Load the user message associated with a run, with DB summary fallback."""
    message = db_session.scalar(
        select(AgentMessage)
        .where(
            AgentMessage.analysis_run_id == run.id,
            AgentMessage.role == "user",
        )
        .order_by(AgentMessage.created_at.desc())
    )
    if message and message.content_text:
        return message.content_text
    return str((run.input_summary_json or {}).get("user_message", ""))


def build_orchestrator_request(
    run: AnalysisRun,
    user_message: str,
    *,
    confirmed_budget_yuan: float | None = None,
) -> OrchestratorRequest:
    """Build an orchestrator request from the persisted run source of truth."""
    if run.session_id is None:
        raise ValueError(f"Run {run.id} has no session_id")
    return OrchestratorRequest(
        teacher_id=0,
        capability_name=run.capability,
        scope=build_run_scope(run),
        user_message=user_message,
        session_id=str(run.session_id),
        db_session_id=run.session_id,
        confirmed_budget_yuan=confirmed_budget_yuan,
    )


async def schedule_analysis_run(
    run: AnalysisRun,
    user_message: str,
    db_session_factory,
    *,
    confirmed_budget_yuan: float | None = None,
) -> asyncio.Task:
    """Register and schedule a persisted AnalysisRun on the ACTIVE MAIN LOOP.

    防重（B2-05 审查）：若该 run 已在活跃调度中，直接返回既有 task。

    B3-11（真实纵向验收修复）：调度必须挂在**应用主事件循环**上。
    TestClient/多 portal 环境下 HTTP 请求的事件循环会随请求结束关闭，
    若 create_task 建在请求 loop 上，execute_run 会在 loop 关闭后被冻结，
    运行永远停在 running。统一通过 event_projection.get_main_event_loop()
    解析主循环（lifespan 启动时捕获）；lifespan 之外的调用回退当前 loop。
    """
    from backend.app.agent.runtime.event_projection import get_main_event_loop

    main_loop = get_main_event_loop()

    async def _schedule_inner() -> asyncio.Task:
        if run.session_id is None:
            raise ValueError(f"Run {run.id} has no session_id")
        request = build_orchestrator_request(
            run,
            user_message,
            confirmed_budget_yuan=confirmed_budget_yuan,
        )
        registry = get_task_registry()

        existing = await registry.get_state(run.id)
        if existing is not None and existing.task is not None and not existing.is_terminal:
            logger.info("运行 %d 已在调度中，跳过重复调度", run.id)
            return existing.task

        registered_state = await registry.register(run.id, run.session_id)
        # ``get_state`` 与 ``register`` 之间可能存在并发调度请求；
        # register 在锁内返回现有状态时，必须复用原任务，不能再次创建
        # execute_run，否则同一个 run 会产生两次模型调用。
        if (
            registered_state is not None
            and getattr(registered_state, "task", None) is not None
            and not getattr(registered_state, "is_terminal", False)
        ):
            return registered_state.task
        ready = asyncio.Event()

        async def _run_after_registration() -> None:
            await ready.wait()
            await execute_run(run.id, run.session_id, request, db_session_factory)

        task = asyncio.create_task(_run_after_registration())
        try:
            await registry.start(run.id, task)
        except BaseException:
            task.cancel()
            raise
        ready.set()
        return task

    current_loop = asyncio.get_running_loop()
    if (
        main_loop is None
        or main_loop.is_closed()
        or not main_loop.is_running()
        or main_loop is current_loop
    ):
        return await _schedule_inner()
    # 主循环与当前（请求）循环不同：把调度提交到主循环并等待其注册完成
    future = asyncio.run_coroutine_threadsafe(_schedule_inner(), main_loop)
    return await asyncio.wrap_future(future)


async def schedule_recovered_runs(db_session_factory) -> list[int]:
    """Schedule queued runs restored from SQLite during application startup."""
    scheduled: list[int] = []
    recovered: list[tuple[AnalysisRun, str, float | None]] = []
    with db_session_factory() as db_session:
        runs = list(
            db_session.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.status == "queued")
                .order_by(AnalysisRun.id)
            )
        )
        for run in runs:
            user_message = get_run_user_message(db_session, run)
            if not user_message:
                RunRepository(db_session).transition_status(run.id, "failed")
                run.error_message = "启动恢复失败：运行缺少原始用户消息"
                logger.error("Recovered run %d has no user message", run.id)
                continue
            confirmed_budget = (run.input_summary_json or {}).get(
                "confirmed_budget_yuan"
            )
            # 不要在 await 调度期间持有这个读取会话。恢复多个运行时，
            # 每个任务都会再打开自己的会话；长时间占用连接会耗尽 SQLite
            # 连接池并把启动误判成“卡死”。
            recovered.append((run, user_message, confirmed_budget))
        db_session.commit()
    for run, user_message, confirmed_budget in recovered:
        await schedule_analysis_run(
            run,
            user_message,
            db_session_factory,
            confirmed_budget_yuan=confirmed_budget,
        )
        scheduled.append(run.id)
    if scheduled:
        logger.info("Scheduled %d recovered analysis runs: %s", len(scheduled), scheduled)
    return scheduled
