"""异步运行执行器

在后台执行 Agent 分析运行，支持：
- 取消检查
- 事件广播
- 结果持久化

被 API 路由调用，通过 TaskRegistry 管理运行状态。

H0-3: 当 AGENT_RUNTIME=harness 时，使用 HarnessRunAdapter 替代 AgentOrchestrator。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .task_registry import get_task_registry

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_HARNESS_TIMEOUT_CODE = "HARNESS_TIMEOUT"
_HARNESS_RUNTIME_CODE = "HARNESS_RUNTIME_ERROR"
_HARNESS_LOCAL_CANCELLED_CODE = "HARNESS_LOCAL_CANCELLED"
_HARNESS_MODEL_STAGE = "model_call"


def _is_batch_child_run(run: Any) -> bool:
    """批量学生诊断仍需保存画像，但不把每名学生的报告塞进聊天记录。"""
    summary = getattr(run, "input_summary_json", None) or {}
    return bool(summary.get("analysis_group_id"))


def _format_harness_failure(
    exc: BaseException,
    *,
    stage: str = _HARNESS_MODEL_STAGE,
    timeout_seconds: float | None = None,
) -> tuple[str, dict[str, object]]:
    """把 Harness 异常转换为非空、可持久化、可直接展示的诊断。

    本地 cancel（harness session/cancel 本地语义、上一次运行被取消、浏览器切走超时）单独识别为
    HARNESS_LOCAL_CANCELLED —— 不当作 RuntimeError，否则前端会显示「请检查模型配置」误导教师。
    """
    raw_message = str(exc).strip()
    msg_lc = raw_message.lower()
    is_timeout = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
    # 本地取消关键字：SDK 把会话级取消作为普通异常抛出时 message 形如
    # "会话 xxx 已被取消 (harness session/cancel 本地语义)"；上层 CancelledError 已被外层处理。
    # 说明：session/cancel 是本地取消语义（本地取消标记），不是真实 RPC 方法。
    _cancel_token = "session" + "/cancel"
    is_local_cancel = (
        not is_timeout
        and (
            "cancel" in msg_lc
            or "已取消" in raw_message
            or _cancel_token in msg_lc
        )
    )
    if is_timeout:
        code = _HARNESS_TIMEOUT_CODE
        seconds = float(timeout_seconds or 0)
        duration = f"{seconds:g} 秒" if seconds > 0 else "限定时间"
        detail = raw_message or f"Harness 模型调用在{duration}内未完成"
        advice = "请检查网络和模型服务状态，或缩短附件上下文后重试。"
    elif is_local_cancel:
        code = _HARNESS_LOCAL_CANCELLED_CODE
        detail = raw_message or "Harness 会话被本地取消"
        advice = "本次会话已被取消（一般是上一次运行被中断）。请直接再次发送即可，无需更改配置。"
    else:
        code = _HARNESS_RUNTIME_CODE
        detail = raw_message or exc.__class__.__name__ or "Harness 运行时发生未知异常"
        advice = "请检查模型配置与运行时状态后重试。"
    display = f"{code}｜{stage}｜{detail} {advice}"
    metadata: dict[str, object] = {
        "code": code,
        "stage": stage,
        "exception_type": exc.__class__.__name__,
        "message": detail,
        "advice": advice,
        "is_local_cancel": is_local_cancel,
    }
    if timeout_seconds is not None:
        metadata["timeout_seconds"] = float(timeout_seconds)
    return display, metadata


def _persist_harness_failure(
    run,
    exc: BaseException,
    *,
    stage: str = _HARNESS_MODEL_STAGE,
    timeout_seconds: float | None = None,
) -> str:
    """写入稳定错误文本与结构化失败元数据，保证 error_message 永不为空。"""
    display, failure = _format_harness_failure(
        exc, stage=stage, timeout_seconds=timeout_seconds,
    )
    summary = dict(run.input_summary_json or {})
    failure["model_name"] = summary.get("model_name") or ""
    failure["provider"] = summary.get("provider") or ""
    summary["failure"] = failure
    run.input_summary_json = summary
    _set_run_status(run, "failed", error_message=display)
    return display


def _harness_turn_timeout(run) -> float:
    """读取运行快照中的回合超时，避免执行器硬编码 120 秒。"""
    summary = dict(run.input_summary_json or {})
    try:
        value = float(summary.get("model_timeout_seconds") or 300.0)
    except (TypeError, ValueError):
        value = 300.0
    return max(30.0, min(value, 600.0))


def _recover_persisted_harness_report(run, session) -> dict | None:
    """读取 Education Bridge 在回合内提交的结构化报告。

    TeachMate 的正式分析要求模型调用 ``submit_report``。某些 Harness
    回合会在工具提交成功后直接进入 idle，而不再产生一条文本型
    ``assistant/message``；此时 SDK 的 ``final_response`` 为空，但报告已经
    由 Bridge 独立进程安全写入 ``analysis_runs.input_summary_json``。读取前
    必须刷新 ORM 缓存，避免把旧的空值误判为回合失败。
    """
    try:
        session.expire(run, ["input_summary_json"])
        summary = dict(run.input_summary_json or {})
    except Exception:
        logger.warning("刷新 Harness 结构化报告失败（run=%s）", getattr(run, "id", "?"), exc_info=True)
        return None
    report = summary.get("structured_answer")
    if not isinstance(report, dict):
        return None
    if not any(key in report for key in ("summary", "findings", "recommendations")):
        return None
    return report


def _is_harness_mode() -> bool:
    """检查是否启用了 Harness 运行时。"""
    return os.getenv("AGENT_RUNTIME", "legacy").strip().lower() == "harness"


def _set_run_status(run, new_status: str, *, error_message: str | None = None) -> None:
    """统一的运行状态更新辅助函数。

    记录状态变更日志，设置 completed_at（对终态），
    避免分散的裸赋值遗漏 completed_at 或 error_message。

    :param run: AnalysisRun ORM 对象
    :param new_status: 新状态
    :param error_message: 可选的错误信息
    """
    old_status = run.status
    # AnalysisRun 只允许通过 RunRepository 的状态机转换。这里保留一个
    # detached ORM 对象的兼容回退，避免单元测试或恢复工具没有 Session 时
    # 失去原有行为；正式执行路径中的对象都会绑定到 SQLAlchemy Session。
    try:
        from sqlalchemy.orm import object_session
        from ..services.agent_runs.repository import RunRepository

        db_session = object_session(run)
        if db_session is not None:
            RunRepository(db_session).transition_status(run.id, new_status)
        else:
            run.status = new_status
            if new_status in ("completed", "failed", "cancelled", "degraded"):
                run.completed_at = datetime.now(timezone.utc)
    except ImportError:
        run.status = new_status
        if new_status in ("completed", "failed", "cancelled", "degraded"):
            run.completed_at = datetime.now(timezone.utc)
    if error_message is not None:
        run.error_message = error_message
    logger.info("运行 %s 状态变更: %s → %s", run.id, old_status, new_status)


async def execute_run(
    run_id: int,
    session_id: int,
    orchestrator_request,
    db_session_factory,
) -> None:
    """在后台执行 Agent 分析运行。

    :param run_id: AnalysisRun ID
    :param session_id: AgentSession ID
    :param orchestrator_request: OrchestratorRequest
    :param db_session_factory: 可调用的 session factory（每次调用返回新 session）
    """
    from ..agent.factory import get_or_create_orchestrator
    from ..agent.orchestrator import OrchestratorResponse
    from ..models.agent_entities import AnalysisRun, AgentMessage
    from ..agent.config import get_agent_config
    from sqlalchemy import select

    registry = get_task_registry()

    # 检查是否已取消
    if await registry.is_cancelled(run_id):
        return

    # H0-3: Harness 路径分发
    if _is_harness_mode():
        await _execute_harness_run(run_id, session_id, orchestrator_request, db_session_factory)
        return

    session: Session = db_session_factory()
    try:
        # 更新 run 状态为 running
        run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
        if run is None:
            await registry.fail(run_id, "运行记录不存在")
            return

        if run.status in ("completed", "failed", "cancelled"):
            logger.info("运行 %d 已终态，跳过", run_id)
            return

        _set_run_status(run, "running")
        # P0-2: 绑定运行时的配置版本号，用于审计 provider 切换
        cfg = get_agent_config()
        run.config_version = cfg.config_version
        run.rules_version = cfg.rules_version
        session.commit()

        # 检查取消
        if await registry.is_cancelled(run_id):
            _set_run_status(run, "cancelled")
            session.commit()
            return

        # 获取编排器
        try:
            orchestrator = await get_or_create_orchestrator()
        except RuntimeError as e:
            _set_run_status(run, "failed", error_message=str(e))
            session.commit()
            await registry.fail(run_id, str(e))
            return

        # 执行分析
        try:
            async def _legacy_event_sink(event_type: str, data: dict) -> None:
                # legacy AgentLoop 原先只在整个 orchestrator.run 返回后才有
                # 结果；把模型/工具边界实时转发到同一条运行事件流，避免过程卡
                # 长时间只停留在“已接收分析任务”。
                await registry.emit_event(run_id, event_type, **(data or {}))

            orchestrator_request.event_sink = _legacy_event_sink
            response: OrchestratorResponse = await orchestrator.run(
                orchestrator_request, db_session=session
            )
        except Exception as e:
            logger.error("运行 %d 执行异常: %s", run_id, e, exc_info=True)
            _set_run_status(run, "failed", error_message=str(e))
            session.commit()
            await registry.fail(run_id, str(e))
            return

        # 检查取消（执行期间可能被取消）
        if await registry.is_cancelled(run_id):
            _set_run_status(run, "cancelled")
            session.commit()
            return

        # P1-11: 编排器要求预算确认 —— 挂起运行等待教师确认
        if response.needs_confirmation:
            _set_run_status(run, "waiting_confirmation")
            run.error_message = ""
            summary = dict(run.input_summary_json or {})
            summary["needs_confirmation"] = True
            summary["confirmation_message"] = response.answer
            run.input_summary_json = summary
            session.commit()
            # 更新 TaskRegistry 状态并发送等待确认事件
            from ..agent.event_types import RUN_WAITING_CONFIRMATION
            await registry.set_status(run_id, "waiting_confirmation")
            await registry.emit_event(run_id, RUN_WAITING_CONFIRMATION, message=response.answer)
            return

        # 更新运行记录
        if response.success:
            _set_run_status(run, "degraded" if response.stop_reason == "degraded" else "completed")
        else:
            _set_run_status(run, "failed", error_message=response.error or "")

        run.actual_cost_yuan = response.cost_yuan
        run.actual_tokens = response.tokens_used

        # P0-6: 持久化工具轨迹
        tool_calls_log: list[dict] = []
        for step in response.steps:
            for tc in step.tool_calls_executed:
                tool_calls_log.append(tc)
        run.tool_calls_json = tool_calls_log

        summary = dict(run.input_summary_json or {})
        summary["stop_reason"] = response.stop_reason
        summary["validation_valid"] = response.validation.valid if response.validation else False
        summary["validation_degraded"] = response.validation.degraded if response.validation else False
        summary["validation_errors"] = response.validation.errors if response.validation else []
        summary["request_ids"] = response.request_ids
        run.input_summary_json = summary
        if (run.capability in {"student_diagnosis", "exam_analysis"}
                and isinstance(response.structured_answer, dict)
                and response.structured_answer):
            from ..services.student_profiles import apply_analysis_report_to_profile
            apply_analysis_report_to_profile(
                session, run=run, report=response.structured_answer, confirmed_by="ai",
            )
        run.completed_at = datetime.now(timezone.utc)
        if (run.capability == "exam_analysis" and
                isinstance(response.structured_answer, dict) and
                response.structured_answer):
            from ..services.agent_analysis.report_snapshot import ensure_report_scope_snapshot
            ensure_report_scope_snapshot(session, run)
        session.commit()
        # P0-6: 持久化证据到 analysis_evidence 表
        if response.evidence:
            from ..models.agent_entities import AnalysisEvidence
            for ev in response.evidence:
                evidence_row = AnalysisEvidence(
                    evidence_id=ev.get("evidence_id", ""),
                    run_id=run_id,
                    evidence_type=ev.get("evidence_type", "db_metric"),
                    local_fact_json=ev.get("fact", {}),
                    source_entity=ev.get("source_entity"),
                    display_summary=ev.get("summary"),
                    contains_personal_data=ev.get("contains_personal_data", False),
                )
                session.add(evidence_row)
            session.commit()

        # P0-6: 持久化 LLM usage 记录
        usage_steps = [step for step in response.steps if step.model_response and step.model_response.usage]
        if usage_steps:
            from ..models.agent_entities import LlmUsageRecord
            from ..agent.config import get_agent_config
            cfg = get_agent_config()
            for i, step in enumerate(usage_steps):
                usage = step.model_response.usage
                # 区分调用来源：首轮分析 / 工具调用轮次 / 校验失败后的修复重试。
                # 此前非首步一律标 repair_retry，会把正常的多轮工具调用误标为修复，
                # 影响计费审计对成本构成的判读。
                if i == 0:
                    stage = "text_analysis"
                elif step.tool_calls_executed:
                    stage = "tool_call"
                else:
                    stage = "repair_retry"
                usage_row = LlmUsageRecord(
                    run_id=run_id,
                    provider=cfg.text_provider,
                    model_name=cfg.text_model_name,
                    stage=stage,
                    input_tokens=usage.input_tokens,
                    cache_read_tokens=getattr(usage, "cache_read_tokens", 0),
                    reasoning_tokens=getattr(usage, "reasoning_tokens", 0),
                    provider_prompt_tokens=getattr(usage, "prompt_tokens", 0) or usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_yuan=step.cost_yuan,
                    provider_request_id=usage.provider_request_id,
                )
                session.add(usage_row)
            session.commit()

        # 旧 Agent 路径没有把运行内 PrivacyMapper 暴露到响应对象，
        # 这里按同一 scope 重建本地映射，仅用于教师端恢复展示。
        from ..agent.privacy import PrivacyMapper
        from ..services.agent_analysis.identity_dict import (
            build_run_identity_dictionary,
            register_identity_into_mapper,
        )
        display_mapper = PrivacyMapper(allow_student_names=True)
        try:
            display_identity = build_run_identity_dictionary(
                session,
                term_id=run.term_id,
                class_id=run.class_id,
                student_id=run.student_id,
            )
            register_identity_into_mapper(display_mapper, display_identity)
        except Exception as exc:
            logger.warning("旧 Agent 路径恢复学生姓名失败: %s", exc)

        display_response_text, display_response_structured = _restore_teacher_output(
            response.answer,
            response.structured_answer if isinstance(response.structured_answer, dict) else None,
            display_mapper,
        )

        # 保存助手消息
        if (response.success or response.stop_reason == "degraded") and not _is_batch_child_run(run):
            assistant_msg = AgentMessage(
                session_id=session_id,
                analysis_run_id=run_id,
                role="assistant",
                content_text=display_response_text,
                structured_answer_json=display_response_structured,
                evidence_ids_json=[
                    e.get("evidence_id", "")
                    for e in (response.evidence or [])
                    if e.get("evidence_id")
                ],
                model_name=get_agent_config().text_model_name,
                provider_request_id=response.request_ids[0] if response.request_ids else None,
            )
            session.add(assistant_msg)
            session.commit()

        # 完成事件 —— degraded 也是终态，通过 run.completed 事件中的 status 字段区分
        if run.status == "failed":
            await registry.fail(
                run_id,
                error=response.error or "运行失败",
                result={
                    "status": "failed",
                    "answer": display_response_text,
                    "stop_reason": response.stop_reason,
                    "cost_yuan": response.cost_yuan,
                    "tokens_used": response.tokens_used,
                },
            )
        else:
            await registry.complete(
                run_id,
                result={
                    "status": run.status,  # "completed" 或 "degraded"
                    "answer": display_response_text,
                    "structured_answer": display_response_structured,
                    "evidence": response.evidence,
                    "cost_yuan": response.cost_yuan,
                    "tokens_used": response.tokens_used,
                    "stop_reason": response.stop_reason,
                },
                status=run.status,
            )

    except asyncio.CancelledError:
        # 被 cancel
        run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
        if run and run.status not in ("completed", "failed", "cancelled"):
            _set_run_status(run, "cancelled")
            session.commit()
            # 发射 run.cancelled 终态事件
            from ..agent.event_types import RUN_CANCELLED
            await registry.emit_event(run_id, RUN_CANCELLED)
        raise
    finally:
        session.close()


async def _execute_harness_run(
    run_id: int,
    session_id: int,
    orchestrator_request,
    db_session_factory,
) -> None:
    """Harness 路径分发（B2-01）。

    - AGENT_RUNTIME=harness：必须使用已配置的常驻 HarnessManager
      （唯一正式生产入口）；未配置/未启动时明确失败，不静默回退；
    - AGENT_RUNTIME=harness-http：显式回退到历史 HTTP 适配器（仅回滚）。
    """
    from ..agent.runtime.harness_runtime import is_harness_http_mode

    if is_harness_http_mode():
        await _execute_harness_run_http(run_id, session_id, orchestrator_request, db_session_factory)
        return

    from ..agent.runtime.harness_manager import get_harness_manager
    runtime = get_harness_manager()
    if runtime is None or not runtime.health().configured:
        registry = get_task_registry()
        error = (
            "Harness 常驻运行时未配置或未启动（AGENT_RUNTIME=harness 需要配置 "
            "模型 API Key 且应用 lifespan 已启动 HarnessManager）"
        )
        from ..models.agent_entities import AnalysisRun
        from sqlalchemy import select
        session: Session = db_session_factory()
        try:
            run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
            if run is not None:
                _set_run_status(run, "failed", error_message=error)
                session.commit()
        finally:
            session.close()
        await registry.fail(run_id, error)
        logger.error("harness 路径失败（未静默回退）：%s", error)
        return

    # 多槽位 HarnessPool 按 run_id 租用独立进程；单槽位继续复用原有
    # HarnessManager，保持旧调用方和测试兼容。
    if hasattr(runtime, "acquire") and hasattr(runtime, "release"):
        queue_started = time.perf_counter()
        manager = await runtime.acquire(run_id)
        queue_wait_ms = int((time.perf_counter() - queue_started) * 1000)
        try:
            await _execute_harness_run_managed(
                run_id, session_id, orchestrator_request, db_session_factory, manager,
                queue_wait_ms=queue_wait_ms,
            )
        finally:
            await runtime.release(run_id)
    else:
        await _execute_harness_run_managed(
            run_id, session_id, orchestrator_request, db_session_factory, runtime,
        )


def _render_turn_prompt(messages: list[dict[str, str]]) -> str:
    """把多轮消息渲染为 Harness 回合的单文本 prompt。

    SDK session/prompt 的 contentBlocks 是文本块；系统规则/正式资料
    以明确 role 前缀拼接，避免模型把历史误当作当前指令。
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"[系统说明]\n{content}")
        elif role == "assistant":
            parts.append(f"[历史助手回答]\n{content}")
        else:
            parts.append(f"[用户]\n{content}")
    return "\n\n".join(parts)


def _try_parse_json(text: str) -> Any:
    """宽松解析模型输出中的 JSON（支持 markdown 代码块包裹）。"""
    import json as _json
    import re as _re

    candidate = text
    match = _re.search(r"```(?:json)?\s*(.*?)\s*```", text, _re.DOTALL)
    if match:
        candidate = match.group(1).strip()
    try:
        return _json.loads(candidate)
    except Exception:
        try:
            return _json.loads(text)
        except Exception:
            return None


def _restore_teacher_output(
    text: str | None,
    structured: dict | None,
    privacy_mapper,
) -> tuple[str, dict]:
    """把已通过隐私校验的匿名结果恢复为本地教师可读结果。

    恢复只发生在 Provider 回合结束之后；模型输入、出站审计和输出校验
    仍然使用匿名副本。匿名引用本身保留在结构化结果中，便于审计和追踪。
    """
    if privacy_mapper is None:
        return text or "", structured or {}
    display_text = privacy_mapper.restore_text_for_display(text or "")
    display_structured = privacy_mapper.restore_for_display(structured or {})
    return display_text, display_structured


async def _repair_turn(
    manager,
    harness_session_id: str,
    privacy_mapper,
    run_id: int,
    session_id: int,
    original_answer: str,
    structured_report: dict | None,
    errors: list[str],
    db_session=None,
    timeout: float = 120.0,
) -> Any:
    """低温修复：带着验证错误清单再走一个 Harness 回合（B3-05）。

    修复 prompt 只包含脱敏文本（模型输出原文已脱敏 + 固定错误文案），
    不携带任何真实身份信息（B3-04：修复重试 prompt 必须脱敏）。
    """
    from ..agent.output_validator import OutputValidator
    from ..agent.outbound_observer import OutboundObserver
    from ..agent.privacy import PrivacyViolationError

    sanitized_answer = privacy_mapper.sanitize_text(original_answer) \
        if privacy_mapper else original_answer
    repair_prompt = (
        "上一轮输出未通过验证。请基于已验证的证据重新生成结构化报告（JSON），"
        "只引用本回合工具返回的 evidence ID：\n"
        f"验证问题：{'；'.join(errors[:5]) or '结构不符合要求'}\n"
        f"上次输出（已脱敏）：{sanitized_answer[:4000]}"
    )
    content_blocks = [{"type": "text", "text": repair_prompt}]

    # 修复回合出站也经过出站观察器
    observer = getattr(manager, "_outbound_observer", None)
    if observer is None:
        observer = OutboundObserver()
        try:
            manager._outbound_observer = observer
        except (AttributeError, TypeError):
            pass
    observer.capture(prompt_text=repair_prompt,
                     privacy_mapper=privacy_mapper,
                     run_id=run_id, session_id=session_id,
                     role="repair")

    try:
        result = await manager.call(
            "session/prompt",
            sessionId=harness_session_id,
            contentBlocks=content_blocks,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("修复回合失败: %s", exc)
        fallback = OutputValidator().validate(
            structured_answer=None, evidence_ledger=None,
            privacy_mapper=privacy_mapper, raw_text="",
            db_session=None, run_id=run_id,
        )
        setattr(fallback, "errors", ["修复回合失败"] + [str(exc)])
        setattr(fallback, "repaired_answer", "")
        return fallback

    result_data = result or {}
    repaired_text = str(
        result_data.get("finalResponse")
        or result_data.get("final_response")
        or result_data.get("content") or ""
    ).strip()

    repaired_struct = _try_parse_json(repaired_text)
    validated = OutputValidator().validate(
        structured_answer=repaired_struct if isinstance(repaired_struct, dict)
        else None,
        evidence_ledger=None,
        privacy_mapper=privacy_mapper,
        raw_text=repaired_text,
        finish_reason=(result or {}).get("finishReason") or "",
        db_session=db_session, run_id=run_id,
    )
    setattr(validated, "repaired_answer", repaired_text if validated.valid else "")
    return validated


def _build_local_fallback_report(db, run_id: int) -> dict:
    """本地确定性降级报告（B3-05）：仅使用已验证 evidence 事实，不含模型内容。"""
    import json as _json

    from ..models.agent_entities import AnalysisEvidence
    from sqlalchemy import select

    findings: list[dict] = []
    try:
        evidences = db.scalars(
            select(AnalysisEvidence).where(AnalysisEvidence.run_id == run_id)
            .order_by(AnalysisEvidence.id)
        ).all()
        for ev in evidences:
            facts = (ev.local_fact_json or {}).get("facts") or []
            title = ev.display_summary or ev.evidence_type or "事实"
            findings.append({
                "title": title[:200],
                "detail": "；".join(str(f.get("text", "")) for f in facts)[:500],
                "evidence_ids": [ev.evidence_id],
            })
    except Exception as exc:
        logger.warning("构建降级报告失败: %s", exc)

    return {
        "answer_type": "exam_analysis",
        "summary": "生成失败，已保留通过验证的可靠统计摘要",
        "findings": findings,
        "recommendations": [],
        "limitations": ["报告因验证失败降级，仅包含已通过验证的证据事实"],
        "scope_snapshot": {},
        "schema_version": "1.0.0",
        "degraded": True,
    }


def resolve_run_harness_session(
    db,
    run,
    db_session,
) -> tuple[str, int]:
    """把 run 绑定到独立 Harness 会话。

    应用数据库负责“滚动摘要 + 最近消息”记忆；Harness 只保存本次运行内的
    工具循环。这样同一份历史不会在两个层级重复增长。恢复已有 run 时复用其
    原 ID，避免重试产生第二条远端会话。
    """
    from .session_mapper import assign_run_harness_session
    from ..models.agent_entities import AgentSession as AS

    agent_session = db.get(AS, db_session.id)
    if agent_session is None:
        # 会话已被删除：使用 run 既有值或生成一次性会话
        if run.harness_session_id:
            return run.harness_session_id, 0
        raise ValueError("AgentSession 不存在，无法解析 Harness 会话")

    if run.harness_session_id:
        return run.harness_session_id, agent_session.context_revision or 0

    hid, context_revision = assign_run_harness_session(db, agent_session.id)
    run.harness_session_id = hid
    db.flush()
    return hid, context_revision


def _build_harness_system_prompt(
    capability_name: str,
    exam_id: int | None,
    db_session=None,
    packet_mode: bool = False,
) -> str:
    """构建 Harness 路径的教学与作用域约束。"""
    prompt = (
        "你是一个专业的教学分析助手。基于给定的正式资料、会话摘要"
        "与历史消息，回答教师的问题。回答要具体、可操作，不得编造"
        "数据；不要提及任何学生真实姓名（只能使用匿名编号）。"
        "回复必须有清晰层次：先给结论摘要，再按主要发现、证据、建议、局限组织内容。"
        "自然语言回复必须使用 Markdown（##/### 标题、短段落、项目符号和必要表格），"
        "每段只表达一个重点，禁止把全部数字和结论挤在一段中；若任务要求结构化 JSON，"
        "严格只输出 JSON，但字段中的文字仍保持短句和分层。"
    )
    if capability_name == "general_chat":
        prompt += (
            " 普通对话默认不查库；如果教师询问当前班级/考试的分组、分层、复习或教学建议，"
            "应先自主选择合适的安全只读工具获取真实统计，再结合结果回答。没有对应考试范围、"
            "问题与教学数据无关或工具返回空数据时，直接说明数据不可用，不要猜测或反复调用失败的工具。"
            "只读结果仅用于本轮回答，禁止修改任何数据库事实；"
            "工具返回的学生信息只能使用匿名编号。"
        )
    if capability_name == "exam_analysis" and exam_id is None:
        prompt += (
            " 当前未绑定数据库中的具体考试。只能基于教师已确认的正式资料"
            "和用户文字进行分析；不得声称读取了数据库考试、成绩或统计数据，"
            "不得创建、修改或补写数据库考试事实，也不得编造统计数字。"
            "如果资料不足，必须明确说明局限。"
        )
    if capability_name == "exam_analysis" and not packet_mode:
        prompt += (
            " 成绩分析必须先调用 get_exam_analysis_bundle，一次取得核心事实和"
            " evidence ID；通常直接据此调用 submit_report，所有 finding 和"
            " recommendation 引用该 evidence ID。禁止用 offset 连续扫描整份附件。"
            "只有数据包明确缺少某个页码细节时才可定向调用 get_formal_attachment，"
            "且最多两次。submit_report 只调用一次；报告保持精炼，优先使用表格和"
            "可执行建议。"
        )
    if packet_mode and capability_name in {
        "exam_analysis", "student_diagnosis", "review_plan",
    }:
        prompt += (
            " 初始消息中的【分析包】已提供服务端核算的核心事实、薄弱点诊断、"
            "行动建议与 evidence ID：直接据此调用一次 submit_report 生成报告，"
            "所有 finding 和 recommendation 引用包中的 evidence ID，"
            "不要调用 get_exam_analysis_bundle 或任何单项统计工具。默认只提交报告；"
            "只有教师明确要求课标、教学依据或原文出处，且该工具已在本回合开放时，"
            "才调用 get_teaching_guidance（整个 run 最多两次）。"
            "如果工具未开放，不要反复尝试，直接依据分析包已有证据完成报告；"
            "并在报告 limitations 中保留分析包给出的局限。"
        )
        if capability_name == "student_diagnosis":
            prompt += (
                " 学生诊断还必须填写 profile_summary：读取分析包中的既有学生画像 JSON，"
                "结合本次证据重写一段连贯的教师可读自然语言。不要输出 student01/student_01，"
                "不要把 findings 或 recommendations 机械拼接，也不要在没有细粒度数据时提及"
                "试卷小分缺失、数据未录入等技术局限；有数据才做具体题型或知识点分析。"
            )
    from ..services.personalization import (
        build_personalization_prompt,
        read_personalization_settings,
    )
    prompt += "\n\n" + build_personalization_prompt(
        read_personalization_settings(db_session)
    )
    return prompt


async def _execute_harness_run_managed(
    run_id: int,
    session_id: int,
    orchestrator_request,
    db_session_factory,
    manager,
    *,
    queue_wait_ms: int = 0,
) -> None:
    """通过常驻 HarnessManager 执行运行（B2-01 正式路径，真实 JSON-RPC 协议）。

    真实协议：
    - initialize / session/prompt / shutdown；
    - session/prompt 只返回 messageId —— 最终答案来自 notification 流
      （agent/inbox/spliced 回执 → session.event → session.status=idle），
      SDK Session.run 封装，本函数只消费回合结果 {finalResponse, ...}；
    - 会话 ID 固定 ``tm-{run_id}-{suffix}``，同一 run 多次调度复用会话。

    隐私与证据门禁（B2-03 / B2-06）：
    - 输入：运行内身份词典 → PrivacyMapper；历史消息、会话摘要、
      正式资料（FormalContextProvider 门禁）全部脱敏后才组 prompt 发给 Provider；
    - 输出：OutputValidator 对最终答案做隐私反向检查（raw_text +
      可解析的结构化答案）；检出真实身份 → 拒绝持久化，run 明确失败；
      错误信息不打印真实姓名/学号/Key/Prompt。
    - 本路径不使用 Agent 工具（无 EvidenceLedger）；若模型输出包含
      结构化报告（findings/recommendations），因无证据引用会被证据契约
      判定失败（不持久化），只允许纯文本答案通过 —— 有证据要求的正式
      报告仍必须走 Python Agent 工具路径。
    """
    from ..models.agent_entities import AnalysisRun, AgentMessage, AgentSession
    from ..agent.event_types import (
        RUN_STARTED, RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED,
    )
    import json as _json  # noqa: F401  （B3-05 降级报告序列化）
    from ..agent.task_registry import get_task_registry
    from ..agent.output_validator import OutputValidator
    from ..agent.privacy import PrivacyMapper
    from ..services.agent_analysis.sessions import SessionService
    from ..services.agent_analysis.identity_dict import (
        build_run_identity_dictionary,
        register_identity_into_mapper,
    )
    from sqlalchemy import select

    started_at = time.perf_counter()
    phase_ms: dict[str, int] = {"queue": queue_wait_ms}
    registry = get_task_registry()
    session: Session = db_session_factory()
    try:
        run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
        if run is None:
            await registry.fail(run_id, "运行记录不存在")
            return
        if run.status in ("completed", "failed", "cancelled"):
            return

        db_session = session.get(AgentSession, session_id)
        if db_session is None:
            _set_run_status(run, "failed", error_message="AgentSession 不存在")
            session.commit()
            await registry.fail(run_id, "AgentSession 不存在")
            return

        _set_run_status(run, "running")
        run.runtime_kind = "harness"
        run.runtime_version = "managed-jsonrpc"

        # 运行创建时的模型快照是本次请求的事实来源。常驻 Harness 若绑定了
        # 不同模型，必须在回合开始前安全重配，禁止静默使用 deepseek-chat。
        run_snapshot = dict(run.input_summary_json or {})
        requested_model = str(run_snapshot.get("model_name") or "").strip()
        active_config = getattr(manager, "_config", None)
        active_model = str(getattr(active_config, "model", "") or "").strip()
        from ..agent.config import get_agent_config
        from ..agent.token_budget import resolve_token_budget
        agent_cfg = get_agent_config()
        token_budget = resolve_token_budget(agent_cfg)
        active_max_tokens = int(getattr(active_config, "max_tokens", 0) or 0)
        active_thinking = (getattr(active_config, "extra_env", {}) or {}).get("DSH_THINKING")
        desired_thinking = "enabled" if agent_cfg.text_thinking_enabled else "disabled"
        # 测试/兼容管理器可能只有 model/scope_root，不支持启动参数热重配。
        # 正式 HarnessConfig 一定含 max_tokens；仅对正式配置执行预算对齐。
        supports_budget_alignment = hasattr(active_config, "max_tokens")
        needs_reconfigure = (
            (requested_model and requested_model != active_model)
            or (
                supports_budget_alignment
                and (
                    active_max_tokens != token_budget.output_limit
                    or active_thinking != desired_thinking
                )
            )
        )
        if needs_reconfigure:
            try:
                from ..agent.runtime.harness_runtime import build_harness_config
                if (requested_model and
                        str(getattr(agent_cfg, "text_model_name", "") or "") != requested_model):
                    raise RuntimeError(
                        f"运行模型快照为 {requested_model}，当前激活模型为 "
                        f"{getattr(agent_cfg, 'text_model_name', '') or 'unknown'}"
                    )
                settings = getattr(getattr(db_session_factory, "kw", None), "settings", None)
                if settings is None:
                    settings = getattr(db_session_factory, "settings", None)
                if settings is None:
                    from ..config import get_settings
                    settings = get_settings()
                target_config = build_harness_config(settings, agent_cfg)
                if target_config is None or (
                    requested_model and target_config.model != requested_model
                ):
                    raise RuntimeError(f"无法为运行模型 {requested_model} 构建 Harness 配置")
                await manager.apply_config(target_config, restart_if_running=True)
                active_config = getattr(manager, "_config", None)
                active_model = str(getattr(active_config, "model", "") or "").strip()
            except Exception as exc:
                error = RuntimeError(f"Harness 模型对齐失败：{exc}")
                message = _persist_harness_failure(
                    run, error, stage="model_alignment",
                )
                session.commit()
                await registry.fail(run_id, message)
                return
        if requested_model and active_model != requested_model:
            error = RuntimeError(
                f"Harness 实际模型 {active_model or 'unknown'} 与运行快照 "
                f"{requested_model} 不一致"
            )
            message = _persist_harness_failure(run, error, stage="model_alignment")
            session.commit()
            await registry.fail(run_id, message)
            return

        # B3-10：run 绑定启动时的配置版本（切换后旧 run 审计信息保持不变）
        bound_version = getattr(getattr(manager, "_config", None),
                                "config_version", None)
        if bound_version is not None:
            run.config_version = bound_version
        budget_snapshot = dict(run.input_summary_json or {})
        budget_snapshot["token_budget"] = {
            "mode": token_budget.mode,
            "prompt_limit": token_budget.prompt_limit,
            "formal_context_limit": token_budget.formal_context_limit,
            "history_limit": token_budget.history_limit,
            "output_limit": token_budget.output_limit,
        }
        run.input_summary_json = budget_snapshot
        # 每个 run 绑定独立 Harness 会话；业务会话连续性由数据库滚动摘要
        # 与最近消息提供。scope 仍通过 Education Bridge 的串行锁隔离，
        # 避免并发分析互相污染。
        try:
            harness_session_id, context_revision = resolve_run_harness_session(
                session, run, db_session,
            )
        except Exception as exc:
            _set_run_status(run, "failed", error_message=str(exc))
            session.commit()
            await registry.fail(run_id, str(exc))
            return
        session.commit()

        if await registry.is_cancelled(run_id):
            _set_run_status(run, "cancelled")
            session.commit()
            return

        # 调度器在创建后台任务时已经发出唯一的 run.started。Harness
        # 启动阶段不再重复写入同一生命周期事件；后续 step/tool/model
        # 通知会通过事件投影实时进入教师端时间线。

        # --- 运行内身份词典 → PrivacyMapper（本地保留原文，外发匿名副本） ---
        privacy_mapper = PrivacyMapper(allow_student_names=True)
        try:
            identity = build_run_identity_dictionary(
                session,
                term_id=db_session.term_id,
                class_id=db_session.class_id,
                student_id=db_session.student_id,
            )
            register_identity_into_mapper(privacy_mapper, identity)
        except Exception as exc:
            logger.warning("构建运行内身份词典失败: %s", exc)

        # --- 多轮上下文（滚动摘要 + 最近消息；正式资料按能力决定是否注入） ---
        from ..services.agent_analysis.sessions import SessionService
        # v3 分析包路径：服务端 preflight 在模型启动前完成数据聚合与知识路由。
        # general_chat 不进入 build_packet（5.4 生效边界）。预计算异常必须
        # fail-closed；只有“未绑定数据库考试”的资料分析才允许 packet=None。
        run_scope = {
            "run_id": run_id,
            "term_id": db_session.term_id,
            "class_id": db_session.class_id,
            "exam_id": db_session.exam_id,
            "student_id": db_session.student_id,
        }
        analysis_packet = None
        tool_policy = None
        packet_text = ""
        data_prep_started = time.perf_counter()
        try:
            from ..agent.analysis_packet import (
                ANALYSIS_CAPABILITIES, build_packet, packet_to_text,
                tool_policy_for,
            )
            if orchestrator_request.capability_name in ANALYSIS_CAPABILITIES:
                analysis_packet = build_packet(
                    session, orchestrator_request.capability_name, run_scope)
            optional_tools: list[str] = []
            # 可选工具按教师明确需求开放，而不是在每个分析回合预先暴露。
            user_text = str(orchestrator_request.user_message or "")
            if (
                orchestrator_request.capability_name == "exam_analysis"
                and db_session.exam_id is None
            ):
                # 无数据库考试时，正式附件是资料分析的唯一补充来源。
                optional_tools.append("get_formal_attachment")
            if re.search(r"课标|教学依据|原文依据|详细依据|出处|为什么这样建议", user_text):
                optional_tools.append("get_teaching_guidance")
            tool_policy = tool_policy_for(
                orchestrator_request.capability_name,
                has_packet=analysis_packet is not None,
                optional_tools=optional_tools,
            )
            packet_text = (
                packet_to_text(analysis_packet) if analysis_packet is not None else ""
            )
            # 有明确数据库范围的分析必须有分析包；不允许悄悄退回旧版
            # 全量数据工具。exam_analysis 无 exam_id 是受控的附件资料模式。
            if (
                orchestrator_request.capability_name in ANALYSIS_CAPABILITIES
                and analysis_packet is None
                and not (
                    orchestrator_request.capability_name == "exam_analysis"
                    and db_session.exam_id is None
                )
            ):
                raise RuntimeError("分析范围内没有足够数据，无法生成分析包")
        except Exception as exc:
            logger.error("分析包/工具策略构建失败，终止本次分析: %s", exc, exc_info=True)
            _set_run_status(run, "failed", error_message="分析数据预处理失败，请检查范围后重试")
            session.commit()
            await registry.fail(run_id, run.error_message)
            return
        phase_ms["data_prep"] = int((time.perf_counter() - data_prep_started) * 1000)
        if analysis_packet is not None:
            # 观测埋点（v3 P4）：包规模/诊断数/策略随 run 持久化，供 token
            # 效率与深查调用率统计。
            try:
                summary_json = dict(run.input_summary_json or {})
                summary_json["packet_stats"] = {
                    "capability": orchestrator_request.capability_name,
                    "diagnostics": len(
                        analysis_packet.get("diagnostics")
                        or analysis_packet.get("priorities") or []),
                    "packet_chars": len(packet_text),
                    "tool_policy": tool_policy,
                }
                run.input_summary_json = summary_json
                session.commit()
            except Exception as exc:
                logger.warning("packet_stats 记录失败: %s", exc)
        system_prompt = _build_harness_system_prompt(
            orchestrator_request.capability_name,
            db_session.exam_id,
            db_session=session,
            packet_mode=analysis_packet is not None,
        )
        try:
            messages = SessionService(session).build_multi_turn_messages(
                session_id=session_id,
                system_prompt=system_prompt,
                user_message=orchestrator_request.user_message,
                privacy_mapper=privacy_mapper,
                current_run_id=run_id,
                formal_context_token_budget=token_budget.formal_context_limit,
                history_token_budget=token_budget.history_limit,
                # 成绩分析通过 get_exam_analysis_bundle / get_formal_attachment
                # 按需读取正式资料，禁止在回合开头再整包重复注入。其他能力
                # 暂时保留原注入路径，避免改变尚未工具化的附件工作流。
                include_formal_context=(
                    orchestrator_request.capability_name != "exam_analysis"
                ),
            )
        except Exception as exc:
            logger.warning("构建多轮上下文失败，降级为单轮+脱敏: %s", exc)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",
                 "content": privacy_mapper.sanitize_text(orchestrator_request.user_message)},
            ]

        # 拼装单文本 prompt（SDK content 块），并保留一份匿名副本供审计用
        prompt_text = _render_turn_prompt(messages)
        if packet_text:
            # v3：分析包放回合末尾（消息流而非 system prompt，保持 system
            # 前缀静态以利用供应商前缀缓存）。包文本同样过运行级脱敏，
            # 与消息内容共用同一 PrivacyMapper。
            packet_text = privacy_mapper.sanitize_text(packet_text)
            prompt_text = prompt_text + "\n\n" + packet_text
        content_blocks = [{"type": "text", "text": prompt_text}]

        # --- B3-03/P1-2：Education Bridge 回合整段串行化 ---
        # 「写 scope → 回合 → 清 scope」持有同一把全局锁；Harness 进程本身
        # 串行执行会话，因此并发分析第二个任务在此排队（状态 queued），
        # 不直接失败；拿锁后恢复 running 并清理一切崩溃残留 scope。
        from ..agent.education_bridge.scope_vault import (
            write_scope, clear_scope, reset_scopes, get_scope_lock,
        )
        scope_root = getattr(manager, "_config", None) and getattr(
            manager._config, "scope_root", ""
        )
        bridge_lock = get_scope_lock(scope_root) if scope_root else None

        if bridge_lock is not None and bridge_lock.locked():
            # 第二个任务排队：显式标记，不失败（前端显示“排队中”）
            _set_run_status(run, "queued",
                            error_message="等待前一个分析完成后排队执行")
            session.commit()
            logger.info("run=%d 排队等待 Education Bridge 串行锁", run_id)

        try:
            if bridge_lock is not None:
                await bridge_lock.acquire()
                if run.status == "queued":
                    # 拿到串行锁：恢复执行状态（前端从“排队中”转为“运行中”）
                    _set_run_status(run, "running")
                    session.commit()
            try:
                if scope_root:
                    # 进程崩溃残留清理：只保留当前回合 scope（串行锁保证安全）
                    reset_scopes(scope_root, keep=harness_session_id)
                    # P1-3：注入失败立即终止回合（不允许无工具继续生成结论）
                    write_scope(
                        scope_root,
                        harness_session_id,
                        run_id=run_id,
                        term_id=db_session.term_id,
                        class_id=db_session.class_id,
                        exam_id=db_session.exam_id,
                        student_id=db_session.student_id,
                        capability=orchestrator_request.capability_name,
                        tool_policy=tool_policy,
                    )

                # --- B3-04：出站观察器（脱敏副本自检 + 审计；泄漏即拒绝出站） ---
                from ..agent.outbound_observer import OutboundObserver
                from ..agent.privacy import PrivacyViolationError
                observer = getattr(manager, "_outbound_observer", None)
                if observer is None:
                    observer = OutboundObserver(
                        storage_dir=Path(scope_root).parent / "outbound-audit"
                        if scope_root else None
                    )
                    try:
                        manager._outbound_observer = observer
                    except (AttributeError, TypeError):
                        pass
                try:
                    observer.capture(
                        prompt_text=prompt_text,
                        privacy_mapper=privacy_mapper,
                        run_id=run_id,
                        session_id=session_id,
                    )
                except PrivacyViolationError as exc:
                    _set_run_status(run, "failed", error_message=str(exc))
                    session.commit()
                    await registry.fail(run_id, str(exc))
                    return

                # --- 执行真实协议回合（进程复用；超时；取消标记） ---
                result_data: dict | None = None
                turn_timeout = _harness_turn_timeout(run)
                model_started = time.perf_counter()
                try:
                    result_data = await manager.call(
                        "session/prompt",
                        sessionId=harness_session_id,
                        contentBlocks=content_blocks,
                        timeout=turn_timeout,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Harness 运行异常: %s", exc or exc.__class__.__name__)
                    phase_ms["model_call"] = int((time.perf_counter() - model_started) * 1000)
                    message = _persist_harness_failure(
                        run,
                        exc,
                        stage=_HARNESS_MODEL_STAGE,
                        timeout_seconds=turn_timeout,
                    )
                    failed_summary = dict(run.input_summary_json or {})
                    failed_summary["timings_ms"] = {
                        **phase_ms,
                        "total": queue_wait_ms + int((time.perf_counter() - started_at) * 1000),
                    }
                    run.input_summary_json = failed_summary
                    # 本地 cancel（harness session/cancel 上一次运行残留 / 浏览器切走超时）：
                    # 不当作 failed；run 状态置为 cancelled、emit RUN_CANCELLED，
                    # 让前端进入 cancelled 而非 failed（错误条消失、不显示"重试"误导按钮）。
                    raw_msg_lc = str(exc or "").lower()
                    is_local_cancel = (
                        not isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                        and ("cancel" in raw_msg_lc or "已取消" in str(exc or ""))
                    )
                    if is_local_cancel:
                        try:
                            from .session_mapper import rotate_harness_session
                            rotate_harness_session(session, db_session.id)
                        except Exception:
                            logger.warning(
                                "本地 cancel 后轮换 Harness 会话失败（run=%s）",
                                run_id, exc_info=True,
                            )
                        _set_run_status(run, "cancelled", error_message=message)
                        session.commit()
                        await registry.cancel(run_id, message=message)
                        return
                    # 超时或本地失效后旧 Harness 会话不可再复用。轮换业务会话
                    # 映射，使工作线程的晚到 notification 只能关联旧的终态 run。
                    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                        try:
                            from .session_mapper import rotate_harness_session
                            rotate_harness_session(session, db_session.id)
                        except Exception:
                            logger.warning(
                                "超时后轮换 Harness 会话失败（run=%s）",
                                run_id,
                                exc_info=True,
                            )
                    session.commit()
                    await registry.fail(run_id, message)
                    return
                finally:
                    phase_ms["model_call"] = int((time.perf_counter() - model_started) * 1000)
            finally:
                # 回合终态（成功/失败/异常/取消）一律清理 scope 文件
                if scope_root:
                    try:
                        clear_scope(scope_root, harness_session_id)
                    except Exception:
                        pass
                if bridge_lock is not None:
                    bridge_lock.release()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # scope 注入/锁等待之外的异常：标记失败并清理（不泄漏终态）
            logger.error("Education Bridge 回合异常: %s", exc, exc_info=True)
            _set_run_status(run, "failed",
                            error_message="教学数据访问范围初始化失败")
            session.commit()
            await registry.fail(run_id, run.error_message)
            return

        # submit_report 是正式分析的唯一结构化落库入口。即使 Harness 随后又返回
        # 一段普通文本，也必须优先使用已经通过 Bridge 写入的报告，不能让普通文本
        # 绕过报告结构、证据引用和隐私校验。
        persisted_report = _recover_persisted_harness_report(run, session)
        answer = str((result_data or {}).get("finalResponse")
                      or (result_data or {}).get("final_response")
                      or (result_data or {}).get("content") or "").strip()
        finish_reason = (result_data or {}).get("finishReason")
        if persisted_report is not None:
            answer = _json.dumps(persisted_report, ensure_ascii=False)
            logger.info("采用 Bridge 已提交的结构化报告进行统一验证（run=%s）", run_id)
        if not answer:
            # 正式 TeachMate 分析可能在 submit_report 工具成功后直接结束
            # 回合，不再附带文本型 assistant/message。报告已经由 Education
            # Bridge 跨进程写入 DB，此时应继续走统一验证/完成链路，不能把
            # 一个已成功提交的报告误报为“没有最终答案”。
            if persisted_report is not None:
                answer = _json.dumps(persisted_report, ensure_ascii=False)
                logger.info(
                    "Harness 回合未返回文本，采用 Bridge 已提交的结构化报告（run=%s）",
                    run_id,
                )
            else:
                reason = f"，finish_reason={finish_reason}" if finish_reason else ""
                error_msg = (
                    "Harness 回合未产生最终答案（可能超时或被取消）"
                    f"{reason}：模型未返回文本，也未提交结构化报告"
                )
                _set_run_status(run, "failed", error_message=error_msg)
                session.commit()
                await registry.fail(run_id, error_msg)
                return

        # --- B3-05：完整输出验证链（JSON→Schema→evidence→隐私→注入→finish） ---
        # 验证通过前不标记 completed、不保存正式 assistant 报告。
        validator = OutputValidator()
        parsed_answer = _try_parse_json(answer)
        structured_report: dict | None = (
            parsed_answer if isinstance(parsed_answer, dict) else None
        )
        # 只要模型已经输出报告 JSON，或文本明确呈现“主要发现/行动建议”等
        # 报告结构，就进入同一条强校验链。否则一段看似报告的纯文本会绕过
        # Schema、证据引用和降级处理，最终被误记为普通成功回复。
        report_like_text = (
            orchestrator_request.capability_name == "exam_analysis"
            and len(re.findall(r"(?:结论摘要|主要发现|关键发现|行动建议|局限|证据与说明)", answer)) >= 2
        )
        is_report = bool(
            (structured_report and any(
                key in structured_report
                for key in ("answer_type", "summary", "findings", "recommendations")
            ))
            or report_like_text
        )

        degraded = False
        retried = False

        def _validate_once(raw_text: str, struct: dict | None) -> Any:
            if not is_report:
                # 纯文本答案：不做结构要求，只跑隐私/注入/finish 子集
                from types import SimpleNamespace as _NS
                errs: list[str] = []
                warns: list[str] = []
                try:
                    validator._validate_privacy(raw_text, {}, privacy_mapper,
                                                errs, warns)
                except Exception:
                    errs.append("输出隐私检查失败，按不通过处理")
                validator._validate_no_injection(raw_text, errs, warns)
                if finish_reason in ("length", "error"):
                    errs.append(
                        f"模型未正常完成 (finish_reason={finish_reason})"
                    )
                if not raw_text.strip():
                    errs.append("输出为空")
                return _NS(valid=not errs, errors=errs, warnings=warns,
                           repaired_answer="", degraded=False)
            return validator.validate(
                structured_answer=struct,
                evidence_ledger=None,
                privacy_mapper=privacy_mapper,
                raw_text=raw_text,
                finish_reason=finish_reason or "",
                db_session=session,
                run_id=run_id,
            )

        validation = _validate_once(answer, structured_report)
        if not validation.valid and is_report:
            # 首次验证失败 → 允许一次低温修复（回顾验证错误，脱敏后重试）
            repair_started = time.perf_counter()
            validation = await _repair_turn(
                manager, harness_session_id, privacy_mapper, run_id, session_id,
                answer, structured_report, validation.errors, db_session=session,
                timeout=_harness_turn_timeout(run),
            )
            phase_ms["report_repair"] = int((time.perf_counter() - repair_started) * 1000)
            retried = True
            if validation.valid:
                repaired = _try_parse_json(validation.repaired_answer or "")
                if isinstance(repaired, dict) and (
                    "findings" in repaired or "recommendations" in repaired
                ):
                    structured_report = repaired
                    answer = validation.repaired_answer

        if not validation.valid:
            # 仍失败 → 本地确定性降级报告（仅来自已验证 evidence 事实）
            if is_report:
                degraded = True
                structured_report = _build_local_fallback_report(session, run_id)
            else:
                # 非结构化输出失败（隐私/注入/finish 违规）→ 拒绝持久化
                _set_run_status(
                    run, "failed",
                    error_message=(
                        "输出验证失败：" + "；".join(validation.errors[:2])
                        + "（不保存为成功报告）"
                    ),
                )
                session.commit()
                await registry.fail(run_id, run.error_message)
                return

        # --- 输出验证通过（或降级）：隐私兜底自检（observable） ---
        # --- 用例可审计的最终答案与用量字段（仅写匿名副本） ---
        _set_run_status(run, "degraded" if degraded else "completed")
        run.actual_tokens = int((result_data or {}).get("tokens_used") or 0)
        run.completed_at = datetime.now(timezone.utc)
        # bridge（独立进程）可能已把 submit_report 的结构化报告写入
        # run.input_summary_json；本 session 的 ORM 缓存看不到跨进程写入，
        # 刷新后读取，避免 structured_answer 被旧值覆盖。
        session.expire(run, ["input_summary_json"])
        summary_dict = dict(run.input_summary_json or {})
        summary_dict["runtime_kind"] = "harness"
        summary_dict["runtime_version"] = "managed-jsonrpc"
        summary_dict["harness_session_id"] = harness_session_id
        summary_dict["context_revision"] = context_revision
        summary_dict["validation_valid"] = validation.valid
        summary_dict["validation_degraded"] = degraded
        summary_dict["validation_errors"] = validation.errors
        summary_dict["repair_retried"] = retried
        summary_dict["timings_ms"] = {
            **phase_ms,
            "total": queue_wait_ms + int((time.perf_counter() - started_at) * 1000),
        }
        # 保存助手消息（已通过验证链 / 降级副本；str 化时保留结构）。
        # 优先采用 education-bridge submit_report 已持久化的结构化报告
        # （summary_json["structured_answer"]），避免用空的 structured_report
        # 覆盖模型真实提交的 findings/recommendations。
        bridge_answer = summary_dict.get("structured_answer") or {}
        final_text = validation.repaired_answer if (retried and validation.repaired_answer) else answer
        display_final_text, display_structured_report = _restore_teacher_output(
            final_text, structured_report, privacy_mapper,
        )
        _, display_bridge_answer = _restore_teacher_output(
            "", bridge_answer, privacy_mapper,
        )
        if display_bridge_answer:
            summary_dict["structured_answer"] = display_bridge_answer
        if (run.capability in {"student_diagnosis", "exam_analysis"}
                and isinstance(bridge_answer, dict) and bridge_answer):
            from ..services.student_profiles import apply_analysis_report_to_profile
            apply_analysis_report_to_profile(
                session, run=run, report=bridge_answer, confirmed_by="ai",
            )
        run.input_summary_json = summary_dict
        if is_report:
            from ..services.agent_analysis.report_snapshot import ensure_report_scope_snapshot
            ensure_report_scope_snapshot(session, run)
        session.commit()
        sa_final = (
            display_structured_report
            if (not degraded and display_structured_report)
            else (display_bridge_answer if display_bridge_answer else (display_structured_report or {}))
        )
        # 批量子运行的结构化结果已经在上面写入对应学生画像；聊天区由任务组
        # 汇总卡统一呈现完成/未完成/失败，不再逐人生成报告消息。
        if not _is_batch_child_run(run):
            session.add(AgentMessage(
                session_id=session_id,
                analysis_run_id=run_id,
                role="assistant",
                content_text=display_final_text if not degraded else (
                    _json.dumps(display_structured_report, ensure_ascii=False)
                    if display_structured_report else display_final_text
                ),
                structured_answer_json=sa_final,
                model_name=requested_model or active_model or "harness",
            ))
            session.commit()

        payload = {"status": "degraded" if degraded else "completed",
                   "answer": display_final_text,
                   "structured_answer": sa_final,
                   "stop_reason": "completed"}
        await registry.complete(run_id, result=payload,
                                status="degraded" if degraded else "completed")
        await registry.emit_event(
            run_id, RUN_COMPLETED,
            message="Harness 运行完成" + ("（降级输出）" if degraded else ""),
        )

    except asyncio.CancelledError:
        run = session.get(AnalysisRun, run_id)
        if run and run.status not in ("completed", "failed", "cancelled"):
            harness_sid = run.harness_session_id
            if harness_sid:
                try:
                    # 本地取消：中断回合等待；notification 链路广播 cancelled
                    await manager.cancel_session(harness_sid)
                except Exception as exc:
                    logger.warning("harness 取消失败: %s", exc)
            _set_run_status(run, "cancelled")
            session.commit()
            await registry.emit_event(run_id, RUN_CANCELLED)
        raise
    finally:
        session.close()


async def _execute_harness_run_http(
    run_id: int,
    session_id: int,
    orchestrator_request,
    db_session_factory,
) -> None:
    """通过 HarnessRunAdapter（HTTP）执行运行——仅 AGENT_RUNTIME=harness-http 显式回滚路径。

    与 execute_run 平行，但使用 Harness Web Server 而非 AgentOrchestrator。
    持久化 runtime_kind、runtime_version、harness_session_id 到 AnalysisRun。
    """
    from ..models.agent_entities import AnalysisRun, AgentMessage, AnalysisEvidence
    from ..agent.event_types import (
        RUN_STARTED, RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED,
        TOOL_STARTED, TOOL_COMPLETED, MODEL_STARTED, MODEL_DELTA,
    )
    from ..agent.runtime.harness_adapter import HarnessRunAdapter, HarnessRunResult
    from sqlalchemy import select

    registry = get_task_registry()
    session: Session = db_session_factory()

    try:
        run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
        if run is None:
            await registry.fail(run_id, "运行记录不存在")
            return

        if run.status in ("completed", "failed", "cancelled"):
            logger.info("运行 %d 已终态，跳过", run_id)
            return

        # 标记为 harness 运行时
        _set_run_status(run, "running")
        run.runtime_kind = "harness-headless"
        from ..agent.runtime.harness_adapter import _HARNESS_VERSION
        run.runtime_version = _HARNESS_VERSION
        session.commit()

        # 检查取消
        if await registry.is_cancelled(run_id):
            _set_run_status(run, "cancelled")
            session.commit()
            return

        # 调度器已发出 run.started；这里仅转发 Harness 的真实过程事件，
        # 避免启动事件在历史和聊天时间线中重复出现。

        # 创建适配器
        base_url = os.getenv("TEACHMATE_BASE_URL", "http://127.0.0.1:8765")
        token = os.getenv("WORKBENCH_TOKEN", "")
        adapter = HarnessRunAdapter(base_url=base_url, token=token)

        # 创建 Harness 会话
        try:
            harness_session_id = await adapter.create_session(agent_preset="teachmate")
            run.harness_session_id = harness_session_id
            session.commit()
        except Exception as e:
            logger.error("Harness 会话创建失败: %s", e)
            _set_run_status(run, "failed", error_message=f"Harness session creation failed: {e}")
            session.commit()
            await registry.fail(run_id, str(run.error_message))
            return

        # 发送用户消息
        user_message = orchestrator_request.user_message
        try:
            result: HarnessRunResult = await adapter.send_message(
                harness_session_id, user_message, timeout=120.0,
            )
        except Exception as e:
            logger.error("Harness 运行异常: %s", e, exc_info=True)
            _set_run_status(run, "failed", error_message=str(e))
            session.commit()
            await registry.fail(run_id, str(e))
            await adapter.close_session(harness_session_id)
            return

        # Harness 的兼容路径拿不到主进程的 PrivacyMapper。按运行 scope
        # 重建一份仅用于教师端展示的映射，避免匿名占位符被直接写入历史消息。
        # 这不会改变发送给模型的内容，也不会把真实姓名外发。
        display_answer = result.answer
        display_structured_answer = result.structured_answer or {}
        try:
            from ..services.agent_analysis.identity_dict import build_display_mapper
            display_mapper = build_display_mapper(
                session,
                term_id=run.term_id,
                class_id=run.class_id,
                student_id=run.student_id,
            )
            display_answer = display_mapper.restore_text_for_display(result.answer or "")
            display_structured_answer = display_mapper.restore_for_display(
                display_structured_answer
            )
        except Exception as exc:
            logger.warning("Harness 结果恢复学生姓名失败: %s", exc, exc_info=True)

        # 转发事件到 TaskRegistry
        for ev in result.events:
            if ev.event_type == TOOL_STARTED:
                tool_name = str(ev.data.get("tool_name") or ev.data.get("tool") or "")
                await registry.emit_event(
                    run_id, TOOL_STARTED,
                    tool_name=tool_name,
                    tool=tool_name,
                    call_id=str(ev.data.get("call_id") or ""),
                    title=ev.data.get("title") or ev.data.get("label") or "",
                    summary=ev.data.get("summary") or ev.data.get("detail") or "",
                    next_action=ev.data.get("next_action") or ev.data.get("nextAction") or "",
                    message=f"工具调用: {tool_name}",
                )
            elif ev.event_type == TOOL_COMPLETED:
                await registry.emit_event(
                    run_id, TOOL_COMPLETED,
                    tool_name=str(ev.data.get("tool_name") or ev.data.get("tool") or ""),
                    tool=str(ev.data.get("tool_name") or ev.data.get("tool") or ""),
                    call_id=str(ev.data.get("call_id") or ""),
                    title=ev.data.get("title") or ev.data.get("label") or "",
                    summary=ev.data.get("summary") or ev.data.get("detail") or "",
                    next_action=ev.data.get("next_action") or ev.data.get("nextAction") or "",
                    message="工具完成",
                )
            elif ev.event_type == MODEL_STARTED:
                await registry.emit_event(
                    run_id, MODEL_STARTED,
                    title=ev.data.get("title") or ev.data.get("label") or "",
                    summary=ev.data.get("summary") or ev.data.get("detail") or "",
                    next_action=ev.data.get("next_action") or ev.data.get("nextAction") or "",
                    message="模型调用",
                )
            elif ev.event_type == MODEL_DELTA:
                await registry.emit_event(run_id, MODEL_DELTA,
                    message=ev.data.get("delta", ""))

        # 更新运行记录
        if result.success:
            _set_run_status(run, "completed")
        else:
            _set_run_status(run, "failed", error_message=result.error or "")

        # U3-04: 从 usage_records 汇总实际 token 和费用
        # 每个 Provider 请求一条 llm_usage_records 记录
        # 未知模型定价时 cost_yuan = None（费用未知）
        total_tokens = 0
        total_cost: float | None = 0.0
        has_unknown_cost = False

        if result.usage_records:
            from ..models.agent_entities import LlmUsageRecord
            for rec in result.usage_records:
                total_tokens += rec.get("input_tokens", 0) + rec.get("cache_read_tokens", 0) + rec.get("output_tokens", 0)
                rec_cost = rec.get("cost_yuan")
                if rec_cost is None:
                    has_unknown_cost = True
                else:
                    total_cost = (total_cost or 0.0) + rec_cost

                usage_row = LlmUsageRecord(
                    run_id=run_id,
                    provider=rec.get("provider", "unknown"),
                    model_name=rec.get("model_name", "unknown"),
                    stage=rec.get("stage", "text_analysis"),
                    input_tokens=rec.get("input_tokens", 0),
                    cache_read_tokens=rec.get("cache_read_tokens", 0),
                    reasoning_tokens=rec.get("reasoning_tokens", 0),
                    provider_prompt_tokens=rec.get("provider_prompt_tokens", 0) or rec.get("input_tokens", 0) + rec.get("cache_read_tokens", 0),
                    output_tokens=rec.get("output_tokens", 0),
                    cost_yuan=rec.get("cost_yuan") or 0.0,
                    provider_request_id=rec.get("provider_request_id"),
                )
                session.add(usage_row)

        run.actual_tokens = total_tokens
        if has_unknown_cost:
            # 有未知定价的请求 → 费用未知
            run.actual_cost_yuan = None
        else:
            run.actual_cost_yuan = total_cost

        summary = dict(run.input_summary_json or {})
        summary["stop_reason"] = result.stop_reason
        summary["runtime_kind"] = result.runtime_kind
        summary["runtime_version"] = result.runtime_version
        summary["harness_session_id"] = harness_session_id
        summary["usage_record_count"] = len(result.usage_records)
        run.input_summary_json = summary
        if (run.capability in {"student_diagnosis", "exam_analysis"}
                and isinstance(display_structured_answer, dict)
                and display_structured_answer):
            from ..services.student_profiles import apply_analysis_report_to_profile
            apply_analysis_report_to_profile(
                session, run=run, report=display_structured_answer, confirmed_by="ai",
            )
        run.completed_at = datetime.now(timezone.utc)
        if (run.capability == "exam_analysis" and
                isinstance(display_structured_answer, dict) and
                display_structured_answer):
            from ..services.agent_analysis.report_snapshot import ensure_report_scope_snapshot
            ensure_report_scope_snapshot(session, run)
        session.commit()

        # 持久化证据
        if result.evidence:
            for ev in result.evidence:
                evidence_row = AnalysisEvidence(
                    evidence_id=ev.get("evidenceId", ev.get("evidence_id", "")),
                    run_id=run_id,
                    evidence_type="db_metric",
                    local_fact_json={"source": ev.get("source", ""), "fact": ev.get("fact", "")},
                    source_entity=ev.get("source"),
                    display_summary=ev.get("fact"),
                    contains_personal_data=False,
                )
                session.add(evidence_row)
            session.commit()

        # 保存助手消息
        if result.success and result.answer and not _is_batch_child_run(run):
            assistant_msg = AgentMessage(
                session_id=session_id,
                analysis_run_id=run_id,
                role="assistant",
                content_text=display_answer,
                structured_answer_json=display_structured_answer,
                evidence_ids_json=[
                    e.get("evidenceId", e.get("evidence_id", ""))
                    for e in (result.evidence or [])
                    if e.get("evidenceId") or e.get("evidence_id")
                ],
                model_name="harness",
            )
            session.add(assistant_msg)
            session.commit()

        # 完成事件
        if run.status == "failed":
            await registry.fail(run_id, result.error or "运行失败", result={
                "status": "failed",
                "answer": display_answer,
                "stop_reason": result.stop_reason,
            })
        else:
            await registry.complete(run_id, result={
                "status": "completed",
                "answer": display_answer,
                "structured_answer": display_structured_answer,
                "evidence": result.evidence,
                "stop_reason": result.stop_reason,
            }, status="completed")

        # 清理 Harness 会话
        await adapter.close_session(harness_session_id)

    except asyncio.CancelledError:
        # 被 cancel —— 尝试向 Harness 发送 session/cancel
        run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
        if run and run.status not in ("completed", "failed", "cancelled"):
            # 发送 session/cancel 到 Harness
            harness_sid = run.harness_session_id
            if harness_sid:
                try:
                    from ..agent.runtime.harness_manager import get_harness_manager
                    manager = get_harness_manager()
                    if manager and manager.is_running:
                        await manager.cancel_session(harness_sid)
                except Exception as e:
                    logger.warning(
                        "Failed to send session/cancel during CancelledError: %s", e
                    )
            _set_run_status(run, "cancelled")
            session.commit()
            # 发射 run.cancelled 终态事件
            await registry.emit_event(run_id, RUN_CANCELLED)
        raise
    finally:
        session.close()
