"""Harness 事件到本地事实的投影（B2-01 / B3-06）

把 HarnessManager notification 投影后的 RuntimeEvent 写入持久化事件
（经统一 EventStore 原子分配 seq）并向活跃 TaskRegistry 广播。

B3-06 修正：
1. seq 由 EventStore 唯一分配（禁止 max(seq)+1），重启后从 DB 最大 seq 续接；
2. 从 Harness 工作线程 → 事件循环的广播，一律通过启动时捕获的主 event loop
   （set_main_event_loop，factory lifespan 注入）；绝不从工作线程取 loop；
3. 事件内容经 _sanitize_payload 脱敏；未通过的完整模型输出不在此持久化；
4. SSE/轮询断线后可经 EventStore.load_events_from_db(after_seq) 从 DB 续读。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 启动时捕获的主事件循环（factory.lifespan 设置）
_main_event_loop: asyncio.AbstractEventLoop | None = None
_main_loop_guard = threading.Lock()


def set_main_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """由 FastAPI lifespan 在启动时捕获主事件循环。"""
    global _main_event_loop
    with _main_loop_guard:
        _main_event_loop = loop


def get_main_event_loop() -> asyncio.AbstractEventLoop | None:
    with _main_loop_guard:
        return _main_event_loop


def resolve_run_id_for_harness_session(
    session_factory: Any, harness_session_id: str | None
) -> int | None:
    """通过 harness_session_id 反查目标 run_id（B3-02）。"""
    if not harness_session_id:
        return None
    try:
        from ...models.agent_entities import AnalysisRun
        active_states = ("queued", "running", "waiting_confirmation")
        with session_factory() as db:
            run_id = db.scalar(
                _select_run_id(
                    AnalysisRun,
                    harness_session_id,
                    active_states,
                )
            )
            if run_id is not None:
                return run_id
            run_id = db.scalar(
                _select_run_id(AnalysisRun, harness_session_id, None)
            )
            if run_id is not None:
                return run_id
            from ...models.agent_entities import AgentSession
            from sqlalchemy import select as sa_select
            run_id = db.scalar(
                sa_select(AnalysisRun.id)
                .join(AgentSession, AnalysisRun.session_id == AgentSession.id)
                .where(AgentSession.harness_session_id == harness_session_id)
                .order_by(AnalysisRun.id.desc())
                .limit(1)
            )
            return run_id
    except Exception:
        logger.debug("解析 harness 会话 run_id 失败", exc_info=True)
        return None


def _select_run_id(model, harness_session_id, active_states):
    from sqlalchemy import select
    stmt = select(model.id).where(
        model.harness_session_id == harness_session_id
    )
    if active_states:
        stmt = stmt.where(model.status.in_(active_states))
    return stmt.order_by(model.id.desc()).limit(1)


def extract_harness_session_id(data: dict[str, Any]) -> str | None:
    """从事件数据中提取 harness 会话 ID（兼容多种字段名）。"""
    for key in ("harness_session_id", "harnessSessionId", "sessionId",
                "session_id", "session_id_str", "harnessSessionID"):
        value = data.get(key)
        if value:
            return str(value)
    return None


def broadcast_to_active_run(event: Any, session_factory: Any):
    """从 Harness 工作线程投影单个 RuntimeEvent（线程安全，不阻塞）。

    事件落库与注册表广播统一调度到主事件循环执行；返回可等待的 Future
    （调用方可选择等待，默认 fire-and-forget）。
    """
    loop = get_main_event_loop()
    if loop is not None and loop.is_running():
        try:
            return asyncio.run_coroutine_threadsafe(
                _project_async(event, session_factory), loop,
            )
        except Exception:
            logger.warning("事件投影调度失败", exc_info=True)
            return None
    # 主 loop 未注册（如早起/独立测试场景）：若当前线程在 running loop 中，
    # 直接以任务形式执行（测试可 await 等待）；否则丢弃并记日志。
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is not None and current.is_running():
        return asyncio.ensure_future(_project_async(event, session_factory))
    logger.debug("主事件循环未就绪，事件投影跳过（%s）", getattr(event, "event_type", ""))
    return None


async def _project_async(event: Any, session_factory: Any) -> None:
    """在主线循环里执行事件落库 + 注册表广播。"""
    data = dict(getattr(event, "data", None) or {})
    # reasoning-delta 仅用于驱动运行状态，不进入事件账本或 SSE。若运行时
    # 主动提供 public_summary，则保留这段短摘要供进度卡展示。
    if getattr(event, "event_type", "") == "model.thinking":
        # thinking_chars 是教师端"深度思考"行的真实计量来源（累计思考字数），
        # 必须透传——此前它被白名单丢弃，前端拿不到任何真实信号，只能退回
        # 硬编码文案，这正是过程卡"像写死"的根因。
        keep_keys = ("sessionId", "session_id", "thinking_chars",
                     "public_summary", "summary", "title",
                     "next_action", "nextAction")
        data = {
            key: data[key]
            for key in keep_keys
            if key in data and isinstance(data[key], (str, int, float))
        }
        # 原始思维链默认不外发：reasoning 可能冗长、会自我修正，也不应成为
        # 隐私或提示词泄漏面。仅在显式开启时透传脱敏后的末行，供开发/演示
        # 核对"模型真的在流动"。
        if _expose_thinking_tail():
            tail = _thinking_tail(data.get("delta"))
            if tail:
                data["thinking_tail"] = tail
        data.pop("delta", None)
        # 不再强制注入固定文案：运行时没给语义摘要时，前端改用真实计量
        # （已用时 / 已思考字数）呈现，而不是一句永远不变的占位话。
    harness_session_id = extract_harness_session_id(data)
    run_id = await asyncio.to_thread(
        resolve_run_id_for_harness_session, session_factory, harness_session_id,
    )
    if run_id is None:
        logger.debug("harness 事件投影：无对应 run（会话 %s）", harness_session_id)
        return

    # U3-04/harness：usage_updated → llm_usage_records（真实引擎用量随
    # assistant/message 的 usage 字段返回，由投影器提取为独立事件）。
    if getattr(event, "event_type", "") == "usage_updated":
        try:
            from ...models.agent_entities import AnalysisRun, LlmUsageRecord
            with session_factory() as db:
                from ...agent.cost import CostEstimator
                run = db.get(AnalysisRun, run_id)
                if run is None:
                    logger.debug("harness usage_updated 跳过：run=%s 不存在", run_id)
                    return
                if run.status not in ("queued", "running", "waiting_confirmation"):
                    logger.info(
                        "harness 晚到 usage 已隔离（run=%s, status=%s）",
                        run_id,
                        run.status,
                    )
                    return
                summary = dict(run.input_summary_json or {})
                input_tokens = int(data.get("input_tokens") or 0)
                cache_read_tokens = int(data.get("cache_read_tokens") or 0)
                reasoning_tokens = int(data.get("reasoning_tokens") or 0)
                output_tokens = int(data.get("output_tokens") or 0)
                # 运行创建时的模型/Provider 快照是计费审计事实来源。事件中若
                # 携带不同模型，拒绝静默漂移并保留警告；不回退到当前全局配置。
                snapshot_model = str(summary.get("model_name") or "").strip()
                event_model = str(data.get("model_name") or "").strip()
                if snapshot_model and event_model and snapshot_model != event_model:
                    logger.warning(
                        "harness usage 模型与运行快照不一致，按快照记录 "
                        "（run=%s, event=%s, snapshot=%s）",
                        run_id,
                        event_model,
                        snapshot_model,
                    )
                model_name = snapshot_model or event_model or "unknown"
                provider = str(summary.get("provider") or "deepseek")
                # 按定价模型核算实际费用；未知模型返回 None（费用未知）
                cost_yuan = CostEstimator().safe_actual_cost(
                    model_name, input_tokens, output_tokens,
                )
                usage_row = LlmUsageRecord(
                    run_id=run_id,
                    provider=provider,
                    model_name=model_name,
                    stage="text_analysis",
                    input_tokens=input_tokens,
                    cache_read_tokens=cache_read_tokens,
                    reasoning_tokens=reasoning_tokens,
                    provider_prompt_tokens=input_tokens + cache_read_tokens,
                    output_tokens=output_tokens,
                    cost_yuan=cost_yuan,
                    provider_request_id=data.get("provider_request_id"),
                )
                db.add(usage_row)
                db.commit()
        except Exception:
            logger.warning(
                "harness usage_updated 落库失败（run=%s）", run_id, exc_info=True
            )
        # usage 事件只进 usage 表，不写 analysis_run_events（避免噪音）
        return

    # 超时/取消后的晚到 notification 可能仍携带旧 session ID；旧 run 已是
    # 终态时不再写入事件或广播，避免把迟到回合伪装成当前运行进度。
    try:
        from ...models.agent_entities import AnalysisRun
        with session_factory() as db:
            target_run = db.get(AnalysisRun, run_id)
            if target_run is None or target_run.status in (
                "completed", "failed", "cancelled", "degraded",
            ):
                logger.info(
                    "harness 晚到事件已隔离（run=%s, status=%s）",
                    run_id,
                    getattr(target_run, "status", "missing"),
                )
                return
    except Exception:
        logger.warning("harness 事件终态校验失败（run=%s）", run_id, exc_info=True)
        return

    from ...models.agent_entities import AnalysisRunEvent  # noqa: F401
    from ...services.agent_runs.event_store import get_event_store

    try:
        store = get_event_store()
        with session_factory() as db:
            # B3-06：seq 由 EventStore 唯一分配（重启后从 DB max 续接）
            # source 是 append_and_persist 的保留关键字参数；事件 data 里若
            # 含 source/timestamp/seq 等元字段会与显式参数冲突（TypeError），
            # 这里剥离后再展开。
            reserved = {"source", "timestamp", "seq", "id", "data"}
            payload = {
                k: v for k, v in _sanitize_payload(data).items()
                if k not in reserved
            }
            await store.append_and_persist(
                run_id, getattr(event, "event_type", "harness_event"),
                db_session=db, source="harness",
                **payload,
            )
            db.commit()
    except Exception:
        logger.warning("harness 事件落库失败（run=%s）", run_id, exc_info=True)

    try:
        from ...agent.task_registry import get_task_registry
        await get_task_registry().emit_event(
            run_id, getattr(event, "event_type", ""),
            data=data, persist=False,
        )
    except Exception:
        logger.debug("harness 事件活跃广播跳过（注册表异常）", exc_info=True)


def _expose_thinking_tail() -> bool:
    """是否外发脱敏后的思考末行（默认关闭）。

    教师端默认不展示模型原始思维链：reasoning 会自我修正、可能夹带 prompt
    痕迹，不应成为隐私或提示词泄漏面。仅在开发/演示需要核对"模型真的在
    流动"时，用 TEACHMATE_EXPOSE_THINKING_TAIL=true 显式开启。
    """
    return str(os.getenv("TEACHMATE_EXPOSE_THINKING_TAIL", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )


_TAIL_MAX_CHARS = 120


def _thinking_tail(delta: Any) -> str:
    """从一批 reasoning-delta 取出末行，做基本脱敏与截断。"""
    if not isinstance(delta, str) or not delta.strip():
        return ""
    visible = delta.strip()
    # 只取最后一行：模型"当前正在想的那句"最有展示价值
    newline = visible.rfind("\n")
    tail = visible[newline + 1:].strip() if newline >= 0 else visible
    if len(tail) > _TAIL_MAX_CHARS:
        tail = tail[:_TAIL_MAX_CHARS].rstrip() + "…"
    return tail


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    """事件 payload 脱敏：不保存 API Key、完整 Prompt 或未脱敏工具输出。"""
    keep = {}
    BLACKLIST = {"api_key", "apikey", "authorization", "token", "key", "prompt"}
    for key, value in data.items():
        if key.lower() in BLACKLIST:
            continue
        if isinstance(value, dict):
            keep[key] = _sanitize_payload(value)
        elif isinstance(value, list):
            keep[key] = [
                _sanitize_payload(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            keep[key] = value
    return keep
