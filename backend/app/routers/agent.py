"""Agent API 路由

提供 AI 教学分析 Agent 的 HTTP 接口。
所有接口需要教师身份认证。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

SSE_HEARTBEAT_INTERVAL_SECONDS = 10.0
"""SSE 心跳注释帧间隔：超过该间隔无事件时向客户端发送 ``: keep-alive`` 保活帧。

前端 teachmate-events.js 的 HEARTBEAT_TIMEOUT_MS=15s；间隔取 10s，
留足网络抖动余量，避免长任务（模型思考/工具执行无事件）被按空闲超时切断。
"""

from ..auth import require_token
from ..agent.config import (
    get_agent_config, AgentConfig, set_runtime_config, get_runtime_config,
    clear_runtime_config, _next_config_version, load_model_profiles,
    save_model_profiles,
)
from ..agent.factory import create_orchestrator, get_or_create_orchestrator, get_provider_info, close_orchestrator
from ..agent.cost import CostEstimator, UnknownModelPricingError
from ..agent.token_budget import resolve_token_budget
from ..agent.registry.capabilities import create_default_capability_registry
from ..agent.providers.base import ModelError
from ..services.agent_runs.scheduler import schedule_analysis_run
from ..services.agent_groups import (
    create_student_group,
    schedule_analysis_group,
    prepare_analysis_group_retry,
    cancel_active_analysis_group,
)
from ..services.multi_agent import merge_structured_answers
from ..models.agent_entities import (
    AgentSession,
    AgentMessage,
    AgentMessageAttachment,
    AnalysisRun,
    AnalysisGroup,
    AnalysisGroupTask,
    AnalysisEvidence,
    BackgroundJob,
    StudentEvaluation,
    EvaluationAuditLog,
    AgentAnalysisSetting,
)
from ..models.entities import Term, Class, Exam, Student, Enrollment
from ..schemas.agent import (
    AgentSessionCreate,
    AgentSessionRead,
    AgentSessionUpdate,
    AgentMessageCreate,
    AgentMessageRead,
    SendMessageResponse,
    AnalysisEstimateRequest,
    AnalysisEstimateResponse,
    AnalysisRunRequest,
    AnalysisGroupCreateRequest,
    AnalysisGroupRead,
    AnalysisGroupDetailRead,
    AnalysisGroupTaskRead,
    AnalysisGroupConfirmRequest,
    AnalysisGroupRetryRequest,
    AnalysisRunRead,
    RunCancelResponse,
    RunRetryResponse,
    RunConfirmResponse,
    JobRead,
    EvaluationCreateRequest,
    EvaluationUpdateRequest,
    EvaluationConfirmRequest,
    EvaluationRead,
    EvaluationAuditEntry,
    StudentProfileRevisionCreate,
    StudentProfileRevisionRead,
    StudentProfileRevisionAction,
    ProviderInfo,
    ProviderSwitchRequest,
    ProviderSwitchResponse,
    AvailableProvider,
    ProviderTestRequest,
    ProviderTestResult,
    ProviderRuntimeStatus,
    SessionPreferences,
    ModelProfile,
    ModelProfileRequest,
    ModelProfilesResponse,
    AttachmentParseRequest,
    AttachmentParseResponse,
    AttachmentParseResultRead,
    AttachmentPromoteRequest,
    AttachmentPromoteResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/agent",
    tags=["agent"],
    dependencies=[Depends(require_token)],
)


def _job_status_payload(job: BackgroundJob) -> dict:
    """把数据库任务状态转换为教师可理解的阶段、进度和重试语义。"""
    checkpoint = dict(job.checkpoint_json or {})
    summary = dict(checkpoint.get("summary") or {})
    conflict_count = int(checkpoint.get("conflicts", summary.get("conflicts", 0)) or 0)
    warning_count = len(checkpoint.get("warnings") or summary.get("warnings") or [])
    if job.status == "queued":
        status_label, display_status = "排队中，等待后台任务开始", "queued"
    elif job.status == "running":
        status_label, display_status = checkpoint.get("phase_label") or "正在分析", "running"
    elif job.status == "waiting_confirmation":
        status_label, display_status = "等待教师确认", "waiting_confirmation"
    elif job.status == "completed" and conflict_count:
        status_label, display_status = "部分成功，存在数据冲突", "partially_success"
    elif job.status == "completed" and warning_count:
        status_label, display_status = "已完成，有数据提示", "completed_with_warnings"
    elif job.status == "completed":
        status_label, display_status = "已完成", "completed"
    elif job.status == "failed":
        status_label, display_status = "失败，可重试", "failed_retryable"
    elif job.status == "cancelled":
        status_label, display_status = "已取消，可重新提交", "cancelled"
    elif job.status == "waiting_ocr":
        status_label, display_status = "等待 OCR 处理", "waiting_ocr"
    else:
        status_label, display_status = job.status, job.status
    payload = JobRead.model_validate(job).model_dump()
    payload.update({
        "checkpoint": checkpoint,
        "phase": checkpoint.get("phase"),
        "phase_label": checkpoint.get("phase_label"),
        "display_status": display_status,
        "status_label": status_label,
        "message": checkpoint.get("message") or job.last_error,
        "processed": checkpoint.get("processed"),
        "total": checkpoint.get("total"),
        "conflict_count": conflict_count,
        "warning_count": warning_count,
        "has_conflicts": bool(conflict_count),
        "can_retry": job.status in {"failed", "cancelled"},
    })
    return payload


def get_session(request: Request):
    """获取数据库会话（FastAPI 依赖，请求结束后自动关闭）。

    使用 yield 模式确保 Session 在正常、异常、取消路径下均被关闭，
    防止连接泄漏。
    """
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


# --- 会话管理 ---

@router.get("/sessions", response_model=list[AgentSessionRead])
def list_sessions(
    term_id: int | None = None,
    include_archived: bool = False,
    search: str | None = None,
    session=Depends(get_session),
):
    """列出 Agent 会话（不含已软删除），支持搜索。"""
    from sqlalchemy import select, or_
    stmt = select(AgentSession).where(AgentSession.deleted_at.is_(None))
    if term_id is not None:
        stmt = stmt.where(AgentSession.term_id == term_id)
    if not include_archived:
        stmt = stmt.where(AgentSession.status != "archived")
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(
                AgentSession.title.ilike(pattern),
                AgentSession.summary.ilike(pattern),
            )
        )
    stmt = stmt.order_by(AgentSession.updated_at.desc())
    return list(session.scalars(stmt))


@router.get("/session-preferences", response_model=SessionPreferences)
def get_session_preferences(term_id: int, session=Depends(get_session)):
    """读取当前学期的置顶会话与文件夹偏好。"""
    if session.get(Term, term_id) is None:
        raise HTTPException(status_code=404, detail={"code": "TERM_NOT_FOUND", "message": "学期不存在"})
    row = session.get(AgentAnalysisSetting, f"teachmate_session_preferences:{term_id}")
    value = row.value_json if row and isinstance(row.value_json, dict) else {}
    return SessionPreferences(term_id=term_id, pinned_session_ids=value.get("pinned_session_ids", []), folders=value.get("folders", []))


@router.put("/session-preferences", response_model=SessionPreferences)
def put_session_preferences(body: SessionPreferences, session=Depends(get_session)):
    """保存当前学期的导航偏好，不接受跨学期会话 ID。"""
    if session.get(Term, body.term_id) is None:
        raise HTTPException(status_code=404, detail={"code": "TERM_NOT_FOUND", "message": "学期不存在"})
    valid_ids = set(session.scalars(select(AgentSession.id).where(AgentSession.term_id == body.term_id, AgentSession.deleted_at.is_(None))))
    pinned = [sid for sid in dict.fromkeys(body.pinned_session_ids) if sid in valid_ids]
    folders = []
    for raw in body.folders:
        if not isinstance(raw, dict):
            continue
        folder_id = str(raw.get("id", "")).strip()[:80]
        name = str(raw.get("name", "")).strip()[:100]
        if not folder_id or not name:
            continue
        ids = [int(sid) for sid in raw.get("sessionIds", []) if str(sid).isdigit() and int(sid) in valid_ids]
        folders.append({"id": folder_id, "name": name, "sessionIds": list(dict.fromkeys(ids)), "collapsed": bool(raw.get("collapsed", False)), "createdAt": raw.get("createdAt")})
    key = f"teachmate_session_preferences:{body.term_id}"
    row = session.get(AgentAnalysisSetting, key)
    value = {"pinned_session_ids": pinned, "folders": folders}
    if row is None:
        session.add(AgentAnalysisSetting(key=key, value_json=value, rules_version="v1.0.0"))
    else:
        row.value_json = value
    session.commit()
    return SessionPreferences(term_id=body.term_id, **value)


@router.post("/sessions", response_model=AgentSessionRead)
def create_session(body: AgentSessionCreate, session=Depends(get_session)):
    """创建 Agent 会话。"""
    from sqlalchemy import select

    if session.get(Term, body.term_id) is None:
        raise HTTPException(status_code=400, detail={"code": "TERM_NOT_FOUND", "message": "学期不存在"})

    if body.class_id is not None:
        classroom = session.get(Class, body.class_id)
        if classroom is None or classroom.term_id != body.term_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "CLASS_SCOPE_MISMATCH", "message": "班级不存在或不属于当前学期"},
            )

    if body.exam_id is not None:
        exam = session.get(Exam, body.exam_id)
        if exam is None or exam.term_id != body.term_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "EXAM_SCOPE_MISMATCH", "message": "考试不存在或不属于当前学期"},
            )

    if body.student_id is not None:
        if session.get(Student, body.student_id) is None:
            raise HTTPException(status_code=400, detail={"code": "STUDENT_NOT_FOUND", "message": "学生不存在"})
        enrollment_stmt = select(Enrollment).where(
            Enrollment.term_id == body.term_id,
            Enrollment.student_id == body.student_id,
        )
        if body.class_id is not None:
            enrollment_stmt = enrollment_stmt.where(Enrollment.class_id == body.class_id)
        if session.scalar(enrollment_stmt) is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "STUDENT_SCOPE_MISMATCH", "message": "学生不属于当前学期或所选班级"},
            )

    agent_session = AgentSession(
        title=body.title, term_id=body.term_id,
        class_id=body.class_id, exam_id=body.exam_id,
        student_id=body.student_id, status="active",
    )
    session.add(agent_session)
    session.commit()
    session.refresh(agent_session)
    return AgentSessionRead.model_validate(agent_session)


@router.patch("/sessions/{session_id}", response_model=AgentSessionRead)
def update_session(session_id: int, body: AgentSessionUpdate, session=Depends(get_session)):
    """更新会话（仅允许标题和归档状态）。"""
    from sqlalchemy import select
    agent_session = session.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    if agent_session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if body.title is not None:
        agent_session.title = body.title
    if body.status is not None:
        agent_session.status = body.status
    session.commit()
    session.refresh(agent_session)
    return AgentSessionRead.model_validate(agent_session)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, session=Depends(get_session)):
    """软删除会话（进入回收站）。"""
    from sqlalchemy import select
    agent_session = session.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    if agent_session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    from datetime import datetime, timezone
    agent_session.deleted_at = datetime.now(timezone.utc)
    agent_session.status = "deleted"
    session.commit()
    return {"ok": True}


@router.post("/sessions/{session_id}/restore", response_model=AgentSessionRead)
def restore_session(session_id: int, session=Depends(get_session)):
    """从回收站恢复会话。"""
    from sqlalchemy import select
    agent_session = session.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
        )
    )
    if agent_session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if agent_session.deleted_at is None:
        raise HTTPException(status_code=400, detail="会话不在回收站中")
    agent_session.deleted_at = None
    agent_session.status = "active"
    session.commit()
    session.refresh(agent_session)
    return AgentSessionRead.model_validate(agent_session)


@router.get("/sessions/trash", response_model=list[AgentSessionRead])
def list_trash_sessions(session=Depends(get_session)):
    """列出回收站会话。"""
    from sqlalchemy import select
    stmt = select(AgentSession).where(AgentSession.deleted_at.is_not(None))
    stmt = stmt.order_by(AgentSession.updated_at.desc())
    return list(session.scalars(stmt))


@router.get("/sessions/{session_id}", response_model=AgentSessionRead)
def get_session_detail(session_id: int, session=Depends(get_session)):
    """获取会话详情。"""
    from sqlalchemy import select
    agent_session = session.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    if agent_session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return AgentSessionRead.model_validate(agent_session)


@router.get("/sessions/{session_id}/messages", response_model=list[AgentMessageRead])
def list_messages(session_id: int, session=Depends(get_session)):
    """获取会话消息列表（附带每条消息关联的附件元数据）。"""
    from sqlalchemy import select
    messages = list(session.scalars(
        select(AgentMessage).where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.created_at)
    ))
    # 批量加载消息-附件关联，避免 N+1；仅取展示所需元数据（不含正文/路径/哈希）。
    from ..models.entities import Attachment
    from ..models.agent_entities import AgentMessageAttachment
    attachments_by_message: dict[int, list[dict]] = {}
    if messages:
        links = list(session.scalars(
            select(AgentMessageAttachment)
            .where(AgentMessageAttachment.message_id.in_([m.id for m in messages]))
            .order_by(AgentMessageAttachment.id)
        ))
        attachment_ids = sorted({ln.attachment_id for ln in links if ln.attachment_id is not None})
        if attachment_ids:
            atts = list(session.scalars(
                select(Attachment).where(Attachment.id.in_(attachment_ids))
            ))
            by_id = {a.id: a for a in atts}
            for ln in links:
                att = by_id.get(ln.attachment_id)
                if att is None:
                    continue
                attachments_by_message.setdefault(ln.message_id, []).append({
                    "id": att.id,
                    "title": att.title,
                    "original_name": att.original_name,
                    "mime_type": att.mime_type,
                    "size_bytes": att.size_bytes,
                })

    # 历史消息可能由旧 Harness 路径保存为匿名占位符。读取会话时按每条
    # 运行的 term/class/student scope 重建本地映射，只在教师端响应中恢复姓名；
    # 数据库仍保留原始匿名文本，发送给模型的上下文也继续走脱敏流程。
    run_ids = {m.analysis_run_id for m in messages if m.analysis_run_id}
    runs_by_id = {}
    if run_ids:
        runs_by_id = {
            run.id: run
            for run in session.scalars(
                select(AnalysisRun).where(AnalysisRun.id.in_(run_ids))
            )
        }
    display_mappers: dict[tuple[int | None, int | None, int | None], object] = {}
    result: list[AgentMessageRead] = []
    for m in messages:
        run = runs_by_id.get(m.analysis_run_id)
        # 批量诊断的子运行仍保留完整结构化结果用于画像写入和审计，但不把
        # 每位学生的报告回显到聊天区；前端通过任务组卡片展示整体状态概览。
        if run is not None and m.role == "assistant" and (run.input_summary_json or {}).get("analysis_group_id"):
            continue
        item = AgentMessageRead.model_validate(m)
        if run is not None:
            item = item.model_copy(update={"capability": run.capability})
        if run is not None and m.role == "assistant":
            scope = (run.term_id, run.class_id, run.student_id)
            mapper = display_mappers.get(scope)
            if mapper is None:
                try:
                    from ..services.agent_analysis.identity_dict import build_display_mapper
                    mapper = build_display_mapper(
                        session,
                        term_id=run.term_id,
                        class_id=run.class_id,
                        student_id=run.student_id,
                    )
                    display_mappers[scope] = mapper
                except Exception as exc:
                    logger.warning("读取历史消息恢复学生姓名失败: %s", exc)
                    mapper = None
            if mapper is not None:
                item = item.model_copy(update={
                    "content_text": mapper.restore_text_for_display(item.content_text or ""),
                    "structured_answer": mapper.restore_for_display(item.structured_answer),
                })
        if attachments_by_message.get(m.id):
            item = item.model_copy(update={"attachments": attachments_by_message[m.id]})
        result.append(item)
    return result


def _scope_for_message(session, agent_session: AgentSession, session_id: int, capability_name: str) -> dict:
    """Resolve the immutable scope snapshot for a newly sent message.

    A follow-up in an otherwise unbound conversation may still refer to the
    exam/class used by the previous analysis in that same conversation.  For
    general chat only, inherit the latest scoped run so read-only tools can
    answer with facts instead of falling back to generic advice.
    """
    scope: dict = {}
    for key in ("exam_id", "class_id", "student_id", "term_id"):
        value = getattr(agent_session, key, None)
        if value is not None:
            scope[key] = value

    if capability_name == "general_chat" and "exam_id" not in scope:
        prior_scoped_run = session.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.session_id == session_id,
                AnalysisRun.exam_id.is_not(None),
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        )
        if prior_scoped_run is not None:
            scope["exam_id"] = prior_scoped_run.exam_id
            if "class_id" not in scope and prior_scoped_run.class_id is not None:
                scope["class_id"] = prior_scoped_run.class_id
            if "student_id" not in scope and prior_scoped_run.student_id is not None:
                scope["student_id"] = prior_scoped_run.student_id
    return scope


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse, status_code=202)
async def send_message(
    request: Request, session_id: int, body: AgentMessageCreate, session=Depends(get_session),
):
    """向会话发送消息，触发 Agent 分析。

    异步执行：保存用户消息 + 创建运行记录 → 返回 202 + run_id。
    客户端通过 GET /runs/{run_id}/events 轮询或 SSE 获取进度。
    """
    from sqlalchemy import select
    from ..models.entities import Attachment

    agent_session = session.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    if agent_session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 附件门禁必须先于消息/运行落库：只接受当前会话学期内、已解析且待教师确认的资料。
    # 追问消息不应要求用户重复上传文件：当本轮没有 attachment_ids 时，继承
    # 同一会话此前已经确认过的附件；本轮有新附件时则在原有上下文基础上合并。
    # 继承在服务端完成，避免刷新页面、切换设备或前端状态丢失后上下文断裂。
    prior_attachment_ids: list[int] = []
    if agent_session.id:
        prior_links = list(session.scalars(
            select(AgentMessageAttachment)
            .join(AgentMessage, AgentMessageAttachment.message_id == AgentMessage.id)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessageAttachment.attachment_id.is_not(None),
            )
            .order_by(AgentMessageAttachment.id.desc())
        ))
        # 最近的关联优先，去重后恢复正向顺序；已删除附件稍后会被自动忽略。
        seen_prior: set[int] = set()
        for link in prior_links:
            attachment_id = int(link.attachment_id)
            if attachment_id not in seen_prior:
                seen_prior.add(attachment_id)
                prior_attachment_ids.append(attachment_id)
        prior_attachment_ids.reverse()

    requested_attachment_ids = list(dict.fromkeys(
        [int(attachment_id) for attachment_id in (body.attachment_ids or [])]
        + prior_attachment_ids
    ))[:20]
    selected_attachments: list[Attachment] = []
    if requested_attachment_ids:
        unique_attachment_ids = requested_attachment_ids
        selected_attachments = list(session.scalars(
            select(Attachment).where(Attachment.id.in_(unique_attachment_ids))
        ))
        by_id = {attachment.id: attachment for attachment in selected_attachments}
        # 当前请求显式携带的 ID 不存在时必须报错；历史关联指向已删除文件时
        # 则跳过该文件，避免一条旧消息阻塞整个会话的后续追问。
        explicit_ids = set(body.attachment_ids or [])
        missing_ids = [
            attachment_id for attachment_id in unique_attachment_ids
            if attachment_id not in by_id and attachment_id in explicit_ids
        ]
        if missing_ids:
            raise HTTPException(
                status_code=404,
                detail={"code": "ATTACHMENT_NOT_FOUND", "message": "部分附件不存在。", "attachment_ids": missing_ids},
            )
        for attachment in selected_attachments:
            if attachment.term_id != agent_session.term_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "ATTACHMENT_SCOPE_MISMATCH", "message": "附件不属于当前会话学期。"},
                )
            parsed = (attachment.metadata_json or {}).get("parsed") or {}
            if parsed.get("status") not in {"pending_review", "confirmed"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ATTACHMENT_NOT_READY",
                        "message": f"附件“{attachment.original_name}”尚未完成解析和确认。",
                        "attachment_id": attachment.id,
                        "status": parsed.get("status") or "not_parsed",
                    },
                )
            if parsed.get("error") or not str(parsed.get("content") or "").strip():
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ATTACHMENT_CONTENT_UNAVAILABLE",
                        "message": f"附件“{attachment.original_name}”没有可供模型读取的正文。",
                        "attachment_id": attachment.id,
                    },
                )

    # Feature flag 门禁：Agent 功能关闭时不得创建运行
    config = get_agent_config()
    if not config.agent_enabled:
        raise HTTPException(
            status_code=403,
            detail={"code": "AGENT_DISABLED", "message": "AI 分析功能未开启，请在设置中启用。"},
        )

    # 确定能力和作用域；先完成全部校验，失败请求不能留下孤立消息。
    # 没有快捷任务就按普通对话处理。即使当前会话上一轮是分析，教师也需要
    # 明确选择分析插件，才重新进入工具调用和证据校验流程。
    capability_name = body.quick_task or "general_chat"

    # 消息级模型绑定：只允许使用已保存且已配置 Key 的档案，避免客户端伪造任意模型。
    selected_profile = None
    if body.model_id:
        selected_profile = next(
            (p for p in load_model_profiles() if str(p.get("id") or "") == body.model_id),
            None,
        )
        if selected_profile is None:
            raise HTTPException(status_code=400, detail={"code": "MODEL_NOT_FOUND", "message": "所选模型档案不存在。"})
        if not _profile_key_configured(selected_profile, config):
            raise HTTPException(status_code=400, detail={"code": "MODEL_KEY_NOT_CONFIGURED", "message": "所选模型尚未配置 API Key。"})
        if config.text_model_profile_id != body.model_id:
            activated, activation_message = await _activate_model_profile(selected_profile)
            if not activated:
                raise HTTPException(status_code=400, detail={"code": "MODEL_ACTIVATE_FAILED", "message": activation_message})
            # 激活后重新读取统一运行快照，后续成本估算、Provider 和 Run 审计使用同一模型。
            config = get_agent_config()

    scope = _scope_for_message(session, agent_session, session_id, capability_name)

    # P1-11: 创建运行前进行成本预估
    cap_registry = create_default_capability_registry()
    capability = cap_registry.get(capability_name)
    if capability is None:
        raise HTTPException(
            status_code=400,
            detail=f"未知能力: {capability_name}",
        )

    required_flag = (
        "exam_ingestion_enabled"
        if capability_name == "exam_ingestion"
        else "text_agent_enabled"
    )
    if not config.is_feature_enabled(required_flag):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "CAPABILITY_DISABLED",
                "message": f"能力 {capability.display_name} 尚未开启。",
            },
        )

    missing_scope = [key for key in capability.scope_requirements if scope.get(key) is None]
    if missing_scope:
        only_exam = missing_scope == ["exam_id"]
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SCOPE_MISSING_EXAM" if only_exam else "SCOPE_REQUIREMENTS_MISSING",
                "message": (
                    "当前任务缺少必要范围：" + "、".join(missing_scope)
                    + "。请先在会话中选择对应对象。"
                ),
                "missing": missing_scope,
            },
        )

    if capability.requires_vision and not config.is_feature_enabled("vision_analysis_enabled"):
        raise HTTPException(
            status_code=403,
            detail={"code": "VISION_DISABLED", "message": "视觉分析功能尚未开启。"},
        )

    from ..agent.token_budget import estimate_text_tokens, resolve_token_budget
    token_budget = resolve_token_budget(config)
    estimator = CostEstimator(budget_soft_limit_yuan=config.budget_soft_limit_yuan)
    attachment_contents = [
        ((attachment.metadata_json or {}).get("parsed") or {}).get("content") or ""
        for attachment in selected_attachments
    ]
    # 长 PDF/Word 正文的 token 估算是纯 CPU 扫描，移出事件循环避免阻塞
    # SSE 心跳和其他请求。
    attachment_tokens = await asyncio.to_thread(
        lambda: sum(estimate_text_tokens(content) for content in attachment_contents)
    )
    est_input = min(
        token_budget.prompt_limit,
        2000 + len(capability.required_tools) * 300 + attachment_tokens,
    )
    est_output = token_budget.output_limit
    try:
        estimate = estimator.estimate_text(
            model_name=config.text_model_name,
            estimated_input_tokens=est_input,
            estimated_output_tokens=est_output,
        )
    except UnknownModelPricingError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MODEL_PRICING_MISSING", "message": str(exc)},
        ) from exc

    needs_confirmation = (
        estimate.estimated_cost_yuan > capability.budget_limit_yuan
    )

    # 用户消息和运行记录在同一事务内创建，并建立精确关联。
    user_msg = AgentMessage(
        session_id=session_id, role="user", content_text=body.content,
    )
    run = AnalysisRun(
        session_id=session_id,
        capability=capability_name,
        term_id=agent_session.term_id,
        class_id=scope.get("class_id"),
        exam_id=scope.get("exam_id"),
        student_id=scope.get("student_id"),
        status="waiting_confirmation" if needs_confirmation else "queued",
        estimated_cost_yuan=estimate.estimated_cost_yuan,
        estimated_tokens=est_input + est_output,
        input_summary_json={
            "user_message": body.content,
            "attachment_ids": [attachment.id for attachment in selected_attachments],
            "model_id": config.text_model_profile_id or body.model_id or "",
            "model_name": config.text_model_name,
            "provider": config.text_provider,
            "thinking_enabled": config.text_thinking_enabled,
            "reasoning_effort": config.text_reasoning_effort,
            "context_length": config.text_context_length,
            "max_output_tokens": config.text_max_output_tokens,
            "token_budget": {
                "mode": token_budget.mode,
                "prompt_limit": token_budget.prompt_limit,
                "formal_context_limit": token_budget.formal_context_limit,
                "history_limit": token_budget.history_limit,
                "output_limit": token_budget.output_limit,
            },
            "model_timeout_seconds": config.model_timeout_seconds,
            "estimated_cost_yuan": estimate.estimated_cost_yuan,
            "requires_confirmation": needs_confirmation,
        },
    )
    session.add(user_msg)
    session.add(run)
    session.flush()
    user_msg.analysis_run_id = run.id

    # 先建立消息-附件关联，再把教师本次明确发送的解析正文标记为 confirmed。
    # 这样 SessionService 在后台运行开始时即可经 FormalContextProvider 注入正文，
    # 同时仍保留上传、解析、教师确认三阶段审计状态。
    confirmed_at = datetime.now(timezone.utc).isoformat()
    for attachment in selected_attachments:
        parsed = dict((attachment.metadata_json or {}).get("parsed") or {})
        if parsed.get("status") != "confirmed":
            parsed["status"] = "confirmed"
            parsed["confirmed_at"] = confirmed_at
            parsed["confirmed_by"] = "teacher_message_send"
            metadata = dict(attachment.metadata_json or {})
            metadata["parsed"] = parsed
            attachment.metadata_json = metadata
        session.add(AgentMessageAttachment(
            message_id=user_msg.id,
            attachment_id=attachment.id,
            purpose="chat_context",
            promoted_to_formal=True,
        ))

    session.commit()
    session.refresh(user_msg)
    session.refresh(run)

    # 只有会调用工具、读取资料或执行校验的任务才展示完整步骤卡。
    # 普通聊天保持自然消息流，避免被分析进度 UI 干扰。
    progress_mode = (
        "full"
        if capability_name != "general_chat" or bool(selected_attachments)
        else "light"
    )

    # P1-11: 需要预算确认时，不启动后台执行，等待教师确认
    if needs_confirmation:
        return SendMessageResponse(
            message_id=user_msg.id,
            run_id=run.id,
            status="waiting_confirmation",
            capability=capability_name,
            progress_mode=progress_mode,
        )

    # 统一入口注入服务器拥有的 run_id，并注册后台执行。
    db_factory = request.app.state.session_factory
    await schedule_analysis_run(run, body.content, db_factory)

    return SendMessageResponse(
        message_id=user_msg.id,
        run_id=run.id,
        status="queued",
        capability=capability_name,
        progress_mode=progress_mode,
    )


# --- 能力查询 ---

@router.get("/capabilities")
def list_capabilities():
    """列出所有可用的分析能力。"""
    registry = create_default_capability_registry()
    return registry.list_for_display()


def _estimate_group_tokens(config, registry, student_count: int, include_exam: bool = False) -> int:
    """按当前模型预算估算批量任务 token；兼容旧记录缺少该字段的情况。"""
    token_budget = resolve_token_budget(config)

    def capability_tokens(capability_name: str) -> int:
        capability = registry.get(capability_name)
        if capability is None:
            return 0
        return min(
            token_budget.prompt_limit,
            2000 + len(capability.required_tools) * 300,
        ) + token_budget.output_limit

    total = capability_tokens("student_diagnosis") * max(0, int(student_count))
    if include_exam:
        total += capability_tokens("exam_analysis")
    return int(total)


def _analysis_group_payload(session, group: AnalysisGroup) -> dict:
    tasks = list(session.scalars(
        select(AnalysisGroupTask)
        .where(AnalysisGroupTask.group_id == group.id)
        .order_by(AnalysisGroupTask.shard_index)
    ))
    payload = AnalysisGroupRead.model_validate(group).model_dump()
    estimated_tokens = (group.scope_snapshot_json or {}).get("estimated_tokens")
    if estimated_tokens is None:
        try:
            estimated_tokens = _estimate_group_tokens(
                get_agent_config(),
                create_default_capability_registry(),
                group.requested_student_count,
                include_exam=str(group.group_type or "") == "exam_plus_students",
            )
        except Exception:
            estimated_tokens = None
    payload["estimated_tokens"] = estimated_tokens
    payload["tasks"] = [AnalysisGroupTaskRead.model_validate(task).model_dump() for task in tasks]
    run_ids = [task.analysis_run_id for task in tasks]
    merged: list[dict] = []
    if run_ids:
        messages = list(session.scalars(
            select(AgentMessage)
            .where(AgentMessage.analysis_run_id.in_(run_ids), AgentMessage.role == "assistant")
            .order_by(AgentMessage.created_at)
        ))
        merged = [dict(message.structured_answer_json or {}) for message in messages if message.structured_answer_json]
    payload["merged_result"] = merge_structured_answers(merged) if merged else None
    return payload


@router.post("/groups", response_model=AnalysisGroupDetailRead, status_code=202)
async def create_analysis_group(
    request: Request,
    body: AnalysisGroupCreateRequest,
    session=Depends(get_session),
):
    """创建批量学生画像任务组，并按并发上限调度独立学生诊断。"""
    from ..models.entities import Enrollment

    agent_session = session.get(AgentSession, body.session_id)
    if agent_session is None or agent_session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if agent_session.term_id != body.term_id:
        raise HTTPException(status_code=409, detail={"code": "TERM_SCOPE_MISMATCH", "message": "会话与任务学期不一致。"})
    if agent_session.class_id is not None and body.class_id != agent_session.class_id:
        raise HTTPException(status_code=409, detail={"code": "CLASS_SCOPE_MISMATCH", "message": "会话与任务班级不一致。"})
    if agent_session.exam_id is not None and body.exam_id != agent_session.exam_id:
        raise HTTPException(status_code=409, detail={"code": "EXAM_SCOPE_MISMATCH", "message": "会话与任务考试不一致。"})
    if agent_session.student_id is not None and any(int(value) != agent_session.student_id for value in body.student_ids):
        raise HTTPException(status_code=409, detail={"code": "STUDENT_SCOPE_MISMATCH", "message": "批量任务不能复用单个学生会话。"})
    exam = session.get(Exam, body.exam_id)
    if exam is None or exam.term_id != body.term_id:
        raise HTTPException(status_code=400, detail={"code": "EXAM_SCOPE_MISMATCH", "message": "考试不存在或不属于当前学期。"})
    if body.class_id is not None:
        classroom = session.get(Class, body.class_id)
        if classroom is None or classroom.term_id != body.term_id:
            raise HTTPException(status_code=400, detail={"code": "CLASS_SCOPE_MISMATCH", "message": "班级不存在或不属于当前学期。"})
    if not get_agent_config().is_feature_enabled("text_agent_enabled"):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_DISABLED", "message": "AI 分析功能尚未开启。"})

    requested_ids = list(dict.fromkeys(int(value) for value in body.student_ids))
    enrollment_stmt = select(Enrollment.student_id).where(
        Enrollment.term_id == body.term_id,
        Enrollment.student_id.in_(requested_ids),
    )
    if body.class_id is not None:
        enrollment_stmt = enrollment_stmt.where(Enrollment.class_id == body.class_id)
    valid_ids = set(session.scalars(enrollment_stmt))
    invalid_ids = [student_id for student_id in requested_ids if student_id not in valid_ids]
    if invalid_ids:
        raise HTTPException(status_code=400, detail={"code": "STUDENT_SCOPE_MISMATCH", "message": "部分学生不属于当前学期或班级。", "student_ids": invalid_ids})

    config = get_agent_config()
    registry = create_default_capability_registry()
    per_student_budget = registry.get("student_diagnosis").budget_limit_yuan
    exam_budget = registry.get("exam_analysis").budget_limit_yuan if body.capability == "exam_plus_students" else 0.0
    estimated = float(per_student_budget * len(valid_ids) + exam_budget)
    estimated_tokens = _estimate_group_tokens(
        config,
        registry,
        len(valid_ids),
        include_exam=body.capability == "exam_plus_students",
    )
    if body.confirmed_budget_yuan is not None and body.confirmed_budget_yuan < estimated:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "GROUP_BUDGET_TOO_LOW",
                "message": "确认预算低于预计成本。",
                "estimated_cost_yuan": estimated,
            },
        )
    if body.require_confirmation:
        confirmed = False
    elif body.confirmed_budget_yuan is None and estimated > config.budget_soft_limit_yuan:
        confirmed = False
    else:
        confirmed = True
    group, tasks, plan = create_student_group(
        session,
        session_id=body.session_id,
        term_id=body.term_id,
        class_id=body.class_id,
        exam_id=body.exam_id,
        student_ids=requested_ids,
        max_concurrency=body.max_concurrency,
        shard_size=body.shard_size,
        include_exam_analysis=body.capability == "exam_plus_students",
        estimated_cost_yuan=estimated,
    )
    group.scope_snapshot_json = {
        **dict(group.scope_snapshot_json or {}),
        "estimated_tokens": estimated_tokens,
    }
    session.commit()
    session.refresh(group)
    if not confirmed:
        group.status = "waiting_confirmation"
        group.error_message = (
            f"执行方案已准备好：{len(valid_ids)} 名学生，预计约 {estimated_tokens:,} tokens。"
            "请由教师确认后再开始。"
        )
        session.commit()
        return _analysis_group_payload(session, group)

    if body.confirmed_budget_yuan is not None:
        group.scope_snapshot_json = {
            **dict(group.scope_snapshot_json or {}),
            "confirmed_budget_yuan": float(body.confirmed_budget_yuan),
        }
        session.commit()
    await schedule_analysis_group(group.id, request.app.state.session_factory)
    return _analysis_group_payload(session, group)


@router.get("/groups/{group_id}", response_model=AnalysisGroupDetailRead)
def get_analysis_group(group_id: int, session=Depends(get_session)):
    group = session.get(AnalysisGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="任务组不存在")
    return _analysis_group_payload(session, group)


@router.get("/groups/session/{session_id}/latest", response_model=AnalysisGroupDetailRead | None)
def get_latest_analysis_group_for_session(session_id: int, session=Depends(get_session)):
    """恢复某个历史对话最近一次批量诊断任务的总览。

    批量诊断的学生明细不会作为聊天消息回显，历史对话需要通过任务组关联
    重新加载总览卡片，否则重新打开对话时只能看到欢迎页。
    """
    group = session.scalar(
        select(AnalysisGroup)
        .join(AnalysisGroupTask, AnalysisGroupTask.group_id == AnalysisGroup.id)
        .join(AnalysisRun, AnalysisRun.id == AnalysisGroupTask.analysis_run_id)
        .where(AnalysisRun.session_id == session_id)
        .order_by(AnalysisGroup.created_at.desc(), AnalysisGroup.id.desc())
    )
    if group is None:
        return None
    return _analysis_group_payload(session, group)


@router.post("/groups/{group_id}/confirm", response_model=AnalysisGroupDetailRead, status_code=202)
async def confirm_analysis_group(
    group_id: int,
    body: AnalysisGroupConfirmRequest,
    request: Request,
    session=Depends(get_session),
):
    """确认任务组总预算并开始等待中的子运行。"""
    group = session.get(AnalysisGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="任务组不存在")
    if group.status != "waiting_confirmation":
        raise HTTPException(status_code=409, detail={"code": "GROUP_NOT_WAITING_CONFIRMATION", "message": "任务组当前不在等待确认状态。"})
    if group.estimated_cost_yuan and body.confirmed_budget_yuan < group.estimated_cost_yuan:
        raise HTTPException(status_code=400, detail={"code": "GROUP_BUDGET_TOO_LOW", "message": "确认预算低于预计成本。"})
    group.scope_snapshot_json = {
        **dict(group.scope_snapshot_json or {}),
        "confirmed_budget_yuan": body.confirmed_budget_yuan,
    }
    group.error_message = None
    group.status = "queued"
    session.commit()
    await schedule_analysis_group(group.id, request.app.state.session_factory)
    return _analysis_group_payload(session, group)


@router.post("/groups/{group_id}/cancel", response_model=AnalysisGroupDetailRead)
async def cancel_analysis_group(group_id: int, request: Request, session=Depends(get_session)):
    from ..agent.task_registry import get_task_registry

    group = session.get(AnalysisGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="任务组不存在")
    tasks = list(session.scalars(select(AnalysisGroupTask).where(AnalysisGroupTask.group_id == group_id)))
    registry = get_task_registry()
    for task in tasks:
        # 组取消时统一由当前路由事务落库，避免 registry.cancel 为每个学生
        # 单独持有 SQLite 写事务；大量并行任务期间这会和子运行写入竞争，
        # 导致“停止任务”卡住或请求失败。
        await registry.cancel(task.analysis_run_id, persist=False)
        run = session.get(AnalysisRun, task.analysis_run_id)
        if run is not None and run.status not in {"completed", "degraded", "failed", "cancelled"}:
            from ..services.agent_runs.repository import RunRepository
            RunRepository(session).transition_status(run.id, "cancelled")
        if task.status not in {"completed", "degraded", "failed", "cancelled"}:
            task.status = "cancelled"
            task.completed_at = datetime.now(timezone.utc)
    group.status = "cancelled"
    group.completed_at = datetime.now(timezone.utc)
    session.commit()
    # 同时停止组级调度协程，防止取消请求返回后外层 gather 继续推进新任务。
    cancel_active_analysis_group(group_id)
    return _analysis_group_payload(session, group)


@router.post("/groups/{group_id}/retry", response_model=AnalysisGroupDetailRead, status_code=202)
async def retry_analysis_group(
    group_id: int,
    body: AnalysisGroupRetryRequest,
    request: Request,
    session=Depends(get_session),
):
    """只重新生成教师选中的失败/未完成学生，已完成画像保持不变。"""
    group = session.get(AnalysisGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="任务组不存在")
    if group.status not in {"completed", "partially_completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail={
            "code": "GROUP_NOT_RETRYABLE",
            "message": "任务组仍在执行或等待确认，请待本轮结束后再重试。",
        })
    selected_tasks, invalid_ids = prepare_analysis_group_retry(session, group, body.student_ids)
    if invalid_ids:
        session.rollback()
        raise HTTPException(status_code=400, detail={
            "code": "GROUP_RETRY_SCOPE_INVALID",
            "message": "只能重新生成当前任务中未完成的学生。",
            "student_ids": invalid_ids,
        })
    if not selected_tasks:
        session.rollback()
        raise HTTPException(status_code=400, detail={
            "code": "GROUP_RETRY_EMPTY",
            "message": "没有可重新生成的学生。",
        })
    session.commit()
    session.refresh(group)
    await schedule_analysis_group(group.id, request.app.state.session_factory)
    return _analysis_group_payload(session, group)


# --- 预算控制开关 ---
# 默认关闭（B3 验收结论：模型用量仅为理论估算、未实测，不拦截用户）；
# 用户可在设置中按需开启严格的「未知模型拒绝 + 超限确认」预算门禁。

@router.get("/budget/status")
def budget_status():
    """查看预算控制状态。"""
    from ..agent.cost import DEFAULT_PRICING, get_budget_control
    return {
        "budget_control_enabled": get_budget_control(),
        "pricing_models": len(DEFAULT_PRICING),
        "note": (
            "预算控制默认关闭：新模型无需价格条目即可运行，不做超限确认；"
            "开启后未知模型将拒绝（503 MODEL_PRICING_MISSING）且超过软上限需用户确认。"
        ),
    }


@router.post("/budget/switch")
def budget_switch(body: dict):
    """切换预算控制开关（进程内生效，重启后回到环境变量/默认值）。"""
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="enabled 必须是布尔值")
    from ..agent.cost import set_budget_control, get_budget_control
    set_budget_control(enabled)
    return {
        "success": True,
        "budget_control_enabled": get_budget_control(),
    }


# --- 成本预估 ---

@router.post("/estimate", response_model=AnalysisEstimateResponse)
def estimate_cost(body: AnalysisEstimateRequest, session=Depends(get_session)):
    """预估分析成本。"""
    config = get_agent_config()
    estimator = CostEstimator(budget_soft_limit_yuan=config.budget_soft_limit_yuan)

    cap_registry = create_default_capability_registry()
    capability = cap_registry.get(body.capability)
    if capability is None:
        raise HTTPException(status_code=400, detail=f"未知能力: {body.capability}")

    est_input = 2000 + len(capability.required_tools) * 500
    est_output = 4096
    try:
        estimate = estimator.estimate_text(
            model_name=config.text_model_name,
            estimated_input_tokens=est_input,
            estimated_output_tokens=est_output,
        )
    except UnknownModelPricingError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MODEL_PRICING_MISSING", "message": str(exc)},
        ) from exc

    return AnalysisEstimateResponse(
        estimated_cost_yuan=estimate.estimated_cost_yuan,
        estimated_tokens=est_input + est_output,
        estimated_duration_seconds=30,
        budget_soft_limit_yuan=config.budget_soft_limit_yuan,
        requires_confirmation=estimate.requires_confirmation,
    )


# --- 分析执行 ---

@router.post("/analyze", status_code=410)
async def run_analysis_deprecated():
    """旧分析接口已废弃。

    所有分析必须通过 POST /sessions/{id}/messages 创建运行，
    作用域由 AgentSession 构建，不接受客户端提交的 term/class/exam/student。
    """
    raise HTTPException(
        status_code=410,
        detail={
            "code": "ENDPOINT_DEPRECATED",
            "message": "此接口已废弃，请使用 POST /api/v1/agent/sessions/{id}/messages 创建分析运行",
        },
    )


@router.get("/runs/{run_id}", response_model=AnalysisRunRead)
def get_run(run_id: int, session=Depends(get_session)):
    """获取分析运行结果。"""
    from sqlalchemy import select
    run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="分析运行不存在")
    from ..services.agent_analysis.report_snapshot import report_freshness
    read = AnalysisRunRead.model_validate(run)
    if run.status in ("completed", "degraded"):
        read.report_freshness = report_freshness(session, run)
    return read


@router.get("/runs/{run_id}/evidence")
def get_evidence(run_id: int, session=Depends(get_session)):
    """获取分析证据。"""
    from sqlalchemy import select
    evidence = list(session.scalars(
        select(AnalysisEvidence).where(AnalysisEvidence.run_id == run_id)
    ))
    return [
        {
            "evidence_id": e.evidence_id,
            "evidence_type": e.evidence_type,
            "source_entity": e.source_entity,
            "display_summary": e.display_summary,
            "local_fact": e.local_fact_json,
        }
        for e in evidence
    ]


def _advance_event_cursor(index: int, ev) -> int:
    """推进 SSE 事件游标。

    游标语义是 EventStore 的**全局持久化 seq**（跨 run 共享单调递增），
    不能用"条数 +1"推进：多 run 并发写入会使 seq 出现跳跃，落后的游标会把
    已推送事件再次取出（重复推送）。仅当事件尚未持久化（seq=None，纯内存
    事件）时才退化为本地序号推进。
    """
    seq = getattr(ev, "seq", None)
    if seq is None:
        return index + 1
    return max(index, seq)


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: int,
    request: Request,
    after: int = Query(default=0, ge=0, description="从第 N 个事件开始返回（轮询增量游标）"),
):
    """获取运行事件。

    优先从内存 TaskRegistry 获取；若内存中不存在（应用重启后），
    从数据库恢复运行状态。

    - Accept: text/event-stream → SSE 流式推送
    - 其他 → 返回 JSON 对象（轮询模式），含 next_after 游标
    """
    from ..agent.event_types import TERMINAL_EVENT_TYPES
    from ..agent.task_registry import get_task_registry

    accept = request.headers.get("accept", "")

    registry = get_task_registry()
    state = await registry.get_state(run_id)

    # 内存中不存在，从数据库恢复
    if state is None:
        db_session = request.app.state.session_factory()
        try:
            from sqlalchemy import select
            run = db_session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
            if run is None:
                raise HTTPException(status_code=404, detail="运行不存在")

            # B3-06：重启/断线后从数据库按 seq 续读历史事件
            from ..services.agent_runs.event_store import get_event_store
            db_events = await get_event_store().load_events_from_db(
                run_id, db_session, after_seq=after,
            )

            # SSE 模式：直接重放历史事件 + 结束标记
            if "text/event-stream" in accept:
                async def replay_stream():
                    for ev in db_events:
                        yield ev.to_sse()
                    yield f"event: done\ndata: {json.dumps({'status': run.status})}\n\n"

                return StreamingResponse(
                    replay_stream(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

            db_status = run.status
            is_terminal = db_status in ("completed", "failed", "cancelled", "degraded")
            return {
                "run_id": run_id,
                "status": db_status,
                "events": [ev.to_dict() for ev in db_events],
                "total_events": (db_events[-1].seq if db_events else 0),
                "next_after": (db_events[-1].seq if db_events else after),
                "has_result": db_status in ("completed", "degraded"),
                "result": None,
                "error": run.error_message,
                "recovered_from_db": True,
                "is_terminal": is_terminal,
            }
        finally:
            db_session.close()

    # SSE 模式
    if "text/event-stream" in accept:
        async def event_stream():
            index = after
            # 先发送已有事件
            events = await registry.get_events(run_id, index)
            for ev in events:
                yield ev.to_sse()
                index = _advance_event_cursor(index, ev)

            # 如果运行未结束，持续推送
            # 心跳注释帧：模型回合可能长时间无事件（思考/工具执行），
            # 中间代理（nginx 等）与前端超时器都依赖周期性字节保活，
            # 否则连接被按空闲超时切断（前端回退轮询）。
            last_yield = time.monotonic()
            while not state.is_terminal:
                await asyncio.sleep(0.5)
                now = time.monotonic()
                if now - last_yield >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                    yield ": keep-alive\n\n"
                    last_yield = now
                events = await registry.get_events(run_id, index)
                for ev in events:
                    yield ev.to_sse()
                    index = _advance_event_cursor(index, ev)
                    last_yield = time.monotonic()

            # 发送剩余事件
            events = await registry.get_events(run_id, index)
            for ev in events:
                yield ev.to_sse()
                index = _advance_event_cursor(index, ev)

            # 发送结束标记
            yield f"event: done\ndata: {json.dumps({'status': state.status})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 轮询模式 —— 返回 next_after 游标
    events = await registry.get_events(run_id, after)
    return {
        "run_id": run_id,
        "status": state.status,
        "events": [
            {
                "event": ev.event_type,
                "timestamp": ev.timestamp,
                "data": ev.data,
                **({"seq": ev.seq} if ev.seq is not None else {}),
            }
            for ev in events
        ],
        "total_events": state.next_after,
        "next_after": state.next_after,
        "has_result": state.result is not None,
        "result": state.result,
        "error": state.error,
        "is_terminal": state.is_terminal,
    }


@router.post("/runs/{run_id}/cancel", response_model=RunCancelResponse)
async def cancel_run(run_id: int, request: Request):
    """取消运行（同步落库 + Harness session/cancel）。"""
    from ..agent.task_registry import get_task_registry
    registry = get_task_registry()

    # 先获取 harness_session_id（在落库 cancelled 之前读取）
    harness_session_id: str | None = None
    db_session = request.app.state.session_factory()
    try:
        from sqlalchemy import select
        run = db_session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
        if run is not None:
            harness_session_id = run.harness_session_id
    finally:
        db_session.close()

    # 向 Harness 发送 session/cancel
    if harness_session_id:
        try:
            from backend.app.agent.runtime.harness_manager import get_harness_manager
            manager = get_harness_manager()
            if manager and manager.is_running:
                await manager.cancel_session(harness_session_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to send session/cancel to Harness for run %d: %s",
                run_id, e,
            )

    # 取消本地 asyncio.Task
    cancelled = await registry.cancel(run_id)

    # 同步更新数据库状态
    db_cancelled = False
    db_session = request.app.state.session_factory()
    try:
        from sqlalchemy import select
        run = db_session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
        if run is not None and run.status not in ("completed", "failed", "cancelled", "degraded"):
            from ..services.agent_runs.repository import RunRepository
            RunRepository(db_session).transition_status(run.id, "cancelled")
            db_session.commit()
            db_cancelled = True
    finally:
        db_session.close()

    if cancelled or db_cancelled:
        return RunCancelResponse(run_id=run_id, cancelled=True, message="运行已取消")
    else:
        state = await registry.get_state(run_id)
        if state is None:
            # 内存中不存在，检查数据库
            db_session = request.app.state.session_factory()
            try:
                from sqlalchemy import select
                run = db_session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
                if run is None:
                    raise HTTPException(status_code=404, detail="运行不存在")
                return RunCancelResponse(
                    run_id=run_id,
                    cancelled=False,
                    message=f"运行已处于终态（{run.status}），无法取消",
                )
            finally:
                db_session.close()
        return RunCancelResponse(
            run_id=run_id,
            cancelled=False,
            message=f"运行已处于终态（{state.status}），无法取消",
        )


@router.post("/runs/{run_id}/retry", response_model=RunRetryResponse)
async def retry_run(
    request: Request, run_id: int, session=Depends(get_session),
):
    """基于原运行的消息重试。

    创建新的运行记录，使用原始消息和作用域。
    """
    from sqlalchemy import select

    original_run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
    if original_run is None:
        raise HTTPException(status_code=404, detail="原始运行不存在")

    # 查找原始用户消息
    messages = list(session.scalars(
        select(AgentMessage).where(
            AgentMessage.session_id == original_run.session_id,
            AgentMessage.analysis_run_id == original_run.id,
        )
        .order_by(AgentMessage.created_at.desc())
    ))
    # 找最近一条 user 消息
    original_msg = None
    for msg in messages:
        if msg.role == "user":
            original_msg = msg
            break

    if original_msg is None:
        raise HTTPException(status_code=400, detail="找不到原始用户消息")

    # 重试继承原运行的完整输入与模型快照。附件正文已通过原用户消息的
    # AgentMessageAttachment 正式晋升，无需重复创建关联，但 attachment_ids
    # 必须保留用于审计和前端诊断。
    retry_summary = dict(original_run.input_summary_json or {})
    retry_summary.update({
        "user_message": original_msg.content_text or "",
        "retry_of_run_id": original_run.id,
    })
    retry_summary.pop("failure", None)
    retry_summary.pop("stop_reason", None)
    retry_summary.pop("validation_errors", None)

    # 构建作用域和后台任务统一由 scheduler 处理，避免路由和恢复路径漂移。
    # 创建新运行
    new_run = AnalysisRun(
        session_id=original_run.session_id,
        capability=original_run.capability,
        term_id=original_run.term_id,
        class_id=original_run.class_id,
        exam_id=original_run.exam_id,
        student_id=original_run.student_id,
        status="queued",
        estimated_cost_yuan=original_run.estimated_cost_yuan,
        estimated_tokens=original_run.estimated_tokens,
        input_summary_json=retry_summary,
    )
    session.add(new_run)
    session.commit()
    session.refresh(new_run)

    # 统一入口注入新运行 ID，并注册后台执行。
    db_factory = request.app.state.session_factory
    await schedule_analysis_run(
        new_run,
        original_msg.content_text or "",
        db_factory,
    )

    return RunRetryResponse(new_run_id=new_run.id, status="queued")


@router.post("/runs/{run_id}/confirm", response_model=RunConfirmResponse)
async def confirm_run(
    request: Request, run_id: int, session=Depends(get_session),
):
    """预算确认后继续执行运行。

    P1-11: 将 waiting_confirmation 状态的运行转为 queued，
    设置 confirmed_budget_yuan，启动后台执行。
    """
    from sqlalchemy import select

    run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")

    if run.status != "waiting_confirmation":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NOT_WAITING_CONFIRMATION",
                "message": f"运行当前状态为 {run.status}，无需确认。",
            },
        )

    # 获取预估成本作为确认预算
    confirmed_budget = run.estimated_cost_yuan or 0.0

    # 查找原始用户消息
    messages = list(session.scalars(
        select(AgentMessage).where(
            AgentMessage.session_id == run.session_id,
            AgentMessage.analysis_run_id == run.id,
        )
        .order_by(AgentMessage.created_at.desc())
    ))
    user_msg = None
    for msg in messages:
        if msg.role == "user":
            user_msg = msg
            break
    user_content = user_msg.content_text if user_msg else (run.input_summary_json or {}).get("user_message", "")
    if not user_content:
        raise HTTPException(
            status_code=409,
            detail={"code": "RUN_INPUT_MISSING", "message": "运行缺少原始消息，无法继续。"},
        )

    # 所有输入校验完成后再转换状态并提交，避免失败请求留下孤立 queued 任务。
    from ..services.agent_runs.repository import RunRepository
    RunRepository(session).transition_status(run.id, "queued")
    summary = dict(run.input_summary_json or {})
    summary["confirmed_budget_yuan"] = confirmed_budget
    run.input_summary_json = summary
    session.commit()
    session.refresh(run)

    # 统一入口注入当前运行 ID，并注册后台执行。
    db_factory = request.app.state.session_factory
    await schedule_analysis_run(
        run,
        user_content,
        db_factory,
        confirmed_budget_yuan=confirmed_budget,
    )

    return RunConfirmResponse(
        run_id=run_id,
        status="queued",
        message=f"预算已确认（{confirmed_budget:.4f} 元），运行已启动",
    )


# --- 试卷录入 ---

class ExamIngestRequest(BaseModel):
    """图片/PDF 录入请求。路径只能是数据目录 attachments 下的文件名。"""
    exam_title: str = Field(min_length=1, max_length=150)
    image_paths: list[str] = Field(default_factory=list, max_length=20)
    pdf_path: str | None = None
    subject: str = Field(default="英语", max_length=50)
    term_id: int | None = Field(default=None, gt=0)
    exam_id: int | None = Field(default=None, gt=0)
    source_attachment_ids: list[int] = Field(default_factory=list, max_length=20)


@router.post("/ingest")
def ingest_exam(body: ExamIngestRequest, request: Request, session=Depends(get_session)):
    """从图片或 PDF 创建试卷草稿；教师确认后才进入正式题目结构。"""
    from ..agent.config import get_agent_config
    from ..services.attachment_security import safe_storage_path
    from ..services.exam_ingestion import ExamIngestionService

    if not get_agent_config().feature_flags.get("exam_ingestion_enabled", False):
        raise HTTPException(status_code=503, detail={"code": "exam_ingestion_disabled", "message": "试卷录入功能未启用"})
    if bool(body.image_paths) == bool(body.pdf_path):
        raise HTTPException(status_code=400, detail={"code": "input_required", "message": "请提供图片或 PDF（只能选择一种）"})
    attachments_dir = request.app.state.settings.data_dir / "attachments"
    try:
        paths = [safe_storage_path(attachments_dir, value) for value in body.image_paths]
        pdf = safe_storage_path(attachments_dir, body.pdf_path) if body.pdf_path else None
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": "输入文件必须位于本地附件目录"})
    service = ExamIngestionService(db_session=session, data_dir=request.app.state.settings.data_dir)
    result = service.ingest_from_pdf(pdf, body.exam_title, body.subject, term_id=body.term_id, exam_id=body.exam_id, source_attachment_ids=body.source_attachment_ids) if pdf else service.ingest_from_images(paths, body.exam_title, body.subject, term_id=body.term_id, exam_id=body.exam_id, source_attachment_ids=body.source_attachment_ids)
    if not result.get("ok"):
        status = 404 if result.get("error") in {"term_not_found", "image_file_not_found", "pdf_file_not_found_or_invalid"} else 422
        raise HTTPException(status_code=status, detail=result)
    return result


# --- 附件解析与晋升 (U4-02) ---

VALID_PURPOSES = {
    "exam_paper", "answer_key", "item_score_sheet",
    "student_answer_sheet", "student_exam_image", "chat_context",
}


@router.post("/attachments/{attachment_id}/parse", response_model=AttachmentParseResponse)
def parse_attachment(
    attachment_id: int,
    body: AttachmentParseRequest,
    request: Request,
    session=Depends(get_session),
):
    """提交附件解析后台任务。

    工作流：上传 → 文件校验 → 后台解析 → 教师校对 → 晋升为正式资料。
    解析结果在教师确认前不进入正式考试事实表。
    """
    from ..models.entities import Attachment
    from ..services.job_worker import submit_job

    # 确保 attachment_id 一致
    if body.attachment_id != attachment_id:
        raise HTTPException(400, "路径中的 attachment_id 与请求体不一致")

    purpose = body.purpose
    if purpose not in VALID_PURPOSES:
        raise HTTPException(400, f"无效的 purpose: {purpose}")

    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "附件不存在")

    # 检查是否已有解析任务在运行
    existing = session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.job_type == "attachment_parse",
            BackgroundJob.scope_json.contains({"attachment_id": attachment_id}),
            BackgroundJob.status.in_(["queued", "running"]),
        )
    )
    if existing is not None:
        return AttachmentParseResponse(
            job_id=existing.id,
            attachment_id=attachment_id,
            status=existing.status,
        )

    # 提交后台解析任务
    data_dir = str(request.app.state.settings.data_dir)
    job_id = submit_job(
        session_factory=request.app.state.session_factory,
        job_type="attachment_parse",
        scope={
            "attachment_id": attachment_id,
            "purpose": purpose,
            "settings_data_dir": data_dir,
        },
        idempotency_key=f"attachment_parse_{attachment_id}",
        retry_waiting_ocr=True,
    )

    return AttachmentParseResponse(
        job_id=job_id,
        attachment_id=attachment_id,
        status="queued",
    )


@router.get("/attachments/{attachment_id}/parse-result", response_model=AttachmentParseResultRead)
def get_parse_result(
    attachment_id: int,
    session=Depends(get_session),
):
    """获取附件解析结果（教师校对用）。"""
    from ..models.entities import Attachment

    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "附件不存在")

    parsed = (attachment.metadata_json or {}).get("parsed", {})
    parse_status = parsed.get("status") or "not_parsed"

    # 解析失败时不返回 content（内容为空），只返回错误信息
    if parse_status == "parse_failed":
        return AttachmentParseResultRead(
            attachment_id=attachment_id,
            format=parsed.get("format", "unknown"),
            content="",
            needs_ocr=False,
            error=parsed.get("error", "解析失败"),
            status=parse_status,
            parsed_at=parsed.get("parsed_at"),
            metadata={},
            pages=[],
        )

    return AttachmentParseResultRead(
        attachment_id=attachment_id,
        format=parsed.get("format", "unknown"),
        content=parsed.get("content", ""),
        needs_ocr=parsed.get("needs_ocr", False),
        error=parsed.get("error"),
        status=parse_status,
        parsed_at=parsed.get("parsed_at"),
        metadata=parsed.get("parse_metadata", {}),
        pages=parsed.get("pages", []),
    )


@router.post("/attachments/{attachment_id}/promote", response_model=AttachmentPromoteResponse)
def promote_attachment(
    attachment_id: int,
    body: AttachmentPromoteRequest,
    request: Request,
    session=Depends(get_session),
):
    """教师确认后晋升附件为正式资料。

    B2-02 校验：
    - session 必须存在、未软删除；
    - 附件必须确实关联该 session（通过 AgentMessage 关联记录）；
    - 附件与 session 的 term 必须一致；
    - 无关联记录时不得返回成功；
    - 重复晋升幂等；
    - 教师修正内容追加保存，原始解析结果保留并可追溯。
    """
    from ..models.entities import Attachment
    from ..models.agent_entities import AgentMessage, AgentMessageAttachment, AgentSession
    from sqlalchemy import select

    if body.attachment_id != attachment_id:
        raise HTTPException(400, "路径中的 attachment_id 与请求体不一致")

    purpose = body.purpose
    if purpose not in VALID_PURPOSES:
        raise HTTPException(400, f"无效的 purpose: {purpose}")

    if body.session_id is None:
        raise HTTPException(400, "晋升必须指定 session_id，禁止无关联晋升")

    # 校验会话存在且未软删除
    agent_session = session.get(AgentSession, body.session_id)
    if agent_session is None:
        raise HTTPException(404, "会话不存在")
    if agent_session.deleted_at is not None or agent_session.status != "active":
        raise HTTPException(409, "会话已删除或不可用，无法晋升附件")

    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "附件不存在")

    # term/scope 一致性：附件必须属于当前 session 的 term
    if agent_session.term_id != attachment.term_id:
        raise HTTPException(409, "附件不属于当前会话学期，无法晋升")

    # 附件-会话关联必须存在
    attachments_for_session = session.scalars(
        select(AgentMessageAttachment).join(
            AgentMessage,
            AgentMessageAttachment.message_id == AgentMessage.id,
        ).where(
            AgentMessageAttachment.attachment_id == attachment_id,
            AgentMessage.session_id == body.session_id,
        )
    ).all()
    if not attachments_for_session:
        raise HTTPException(404, "该附件与当前会话没有关联记录，无法晋升")

    metadata = dict(attachment.metadata_json or {})
    parsed = metadata.get("parsed", {})

    # 检查是否已解析
    if not parsed:
        raise HTTPException(409, "附件尚未解析，无法晋升")

    # B3-07：解析失败/含错误正文的附件不得晋升为 confirmed
    if parsed.get("status") == "parse_failed" or parsed.get("error"):
        raise HTTPException(
            409, "附件解析失败，无法晋升为正式资料（避免把坏正文当事实）",
        )

    # B3-07：文件必须真实存在才允许晋升（文件丢失不得晋升）
    try:
        from ..services.attachment_security import safe_storage_path
        settings = request.app.state.settings
        stored = safe_storage_path(settings.attachments_dir, attachment.storage_name)
        if not stored.is_file():
            raise HTTPException(409, "附件文件已丢失，无法晋升为正式资料")
    except FileNotFoundError:
        raise HTTPException(409, "附件存储路径非法，无法晋升")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, "附件文件校验失败，无法晋升")

    # 教师修正：追加保留原文，并记录可追溯历史（B2-02）
    parsed_working = dict(parsed)
    if body.corrections:
        history = list(parsed_working.get("corrections_history") or [])
        history.append({
            "corrected_at": datetime.now(timezone.utc).isoformat(),
            "corrections": body.corrections,
        })
        parsed_working["corrections_history"] = history
        parsed_working["corrected"] = True
        correction_text = str(body.corrections)
        parsed_working["content"] = (str(parsed_working.get("content", ""))
                                     + f"\n\n[教师修正]\n{correction_text}")

    # 标记为已确认（B3-07：显式重建 dict + 先置 None 再赋值，确保 JSON 变更被追踪）
    parsed_working["status"] = "confirmed"
    parsed_working["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    metadata_new = dict(attachment.metadata_json or {})
    metadata_new["parsed"] = parsed_working
    attachment.metadata_json = None
    attachment.metadata_json = metadata_new

    # 幂等晋升：已 promoted 的关联保持 True；未标记的标记为 True
    promoted_count = 0
    for ma in attachments_for_session:
        if not ma.promoted_to_formal:
            ma.promoted_to_formal = True
            ma.purpose = purpose
            promoted_count += 1

    session.commit()

    logger.info(
        "附件 #%d 晋升为正式资料（session=%d, purpose=%s, 新晋升=%d, 关联=%d）",
        attachment_id, body.session_id, purpose, promoted_count, len(attachments_for_session),
    )

    return AttachmentPromoteResponse(
        attachment_id=attachment_id,
        promoted=True,
        message="附件已晋升为正式资料，Agent 可引用",
    )


# --- 后台任务 ---

@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job_status(job_id: int, session=Depends(get_session)):
    """获取后台任务状态。"""
    from sqlalchemy import select
    job = session.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _job_status_payload(job)


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: int, request: Request, session=Depends(get_session)):
    """将失败/取消的后台任务重新入队，供长任务页面提供明确重试入口。"""
    job = session.get(BackgroundJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    worker = getattr(request.app.state, "job_worker", None)
    if worker is None or not worker.retry_job(job_id):
        raise HTTPException(status_code=409, detail="当前任务不可重试")
    session.expire(job)
    refreshed = session.get(BackgroundJob, job_id)
    return _job_status_payload(refreshed)


# --- 学生评价 ---

def _write_audit(session, evaluation_id: int, action: str, detail: dict | None = None) -> None:
    """追加审计日志。追加式写入，不修改历史。"""
    entry = EvaluationAuditLog(
        evaluation_id=evaluation_id,
        action=action,
        actor="teacher",
        detail_json=detail or {},
    )
    session.add(entry)


@router.get("/evaluations", response_model=list[EvaluationRead])
def list_evaluations(
    term_id: int | None = None,
    student_id: int | None = None,
    status: str | None = None,
    session=Depends(get_session),
):
    """列出学生评价，支持按学期、学生、状态筛选。"""
    from sqlalchemy import select
    stmt = select(StudentEvaluation)
    if term_id is not None:
        stmt = stmt.where(StudentEvaluation.term_id == term_id)
    if student_id is not None:
        stmt = stmt.where(StudentEvaluation.student_id == student_id)
    if status is not None:
        stmt = stmt.where(StudentEvaluation.status == status)
    stmt = stmt.order_by(StudentEvaluation.created_at.desc())
    return list(session.scalars(stmt))


@router.post("/evaluations", response_model=EvaluationRead, status_code=201)
def create_evaluation(
    body: EvaluationCreateRequest, session=Depends(get_session),
):
    """从 Agent 报告草稿创建学生评价。

    保留 AI 原文，状态为 draft，等待教师编辑和确认。
    """
    evaluation = StudentEvaluation(
        student_id=body.student_id,
        term_id=body.term_id,
        exam_id=body.exam_id,
        ai_original_text=body.ai_original_text,
        teacher_confirmed_text=None,
        evidence_snapshot_json=body.evidence_snapshot,
        analysis_run_id=body.analysis_run_id,
        status="draft",
    )
    session.add(evaluation)
    session.flush()
    _write_audit(session, evaluation.id, "created", {
        "student_id": body.student_id,
        "term_id": body.term_id,
        "exam_id": body.exam_id,
        "analysis_run_id": body.analysis_run_id,
    })
    session.commit()
    session.refresh(evaluation)
    return EvaluationRead.model_validate(evaluation)


@router.get("/evaluations/{evaluation_id}", response_model=EvaluationRead)
def get_evaluation_by_id(
    evaluation_id: int, session=Depends(get_session),
):
    """获取单个评价详情。"""
    from sqlalchemy import select
    evaluation = session.scalar(
        select(StudentEvaluation).where(StudentEvaluation.id == evaluation_id)
    )
    if evaluation is None:
        raise HTTPException(status_code=404, detail="评价不存在")
    return EvaluationRead.model_validate(evaluation)


@router.patch("/evaluations/{evaluation_id}", response_model=EvaluationRead)
def update_evaluation(
    evaluation_id: int,
    body: EvaluationUpdateRequest,
    session=Depends(get_session),
):
    """教师编辑评价草稿。

    只允许编辑 teacher_confirmed_text（预览文本），不可修改 AI 原文。
    仅 draft 状态的评价可编辑。
    """
    from sqlalchemy import select
    evaluation = session.scalar(
        select(StudentEvaluation).where(StudentEvaluation.id == evaluation_id)
    )
    if evaluation is None:
        raise HTTPException(status_code=404, detail="评价不存在")
    if evaluation.status not in ("draft",):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVALUATION_NOT_EDITABLE",
                "message": f"评价当前状态为 {evaluation.status}，仅 draft 状态可编辑。",
            },
        )

    old_text = evaluation.teacher_confirmed_text
    evaluation.teacher_confirmed_text = body.teacher_confirmed_text
    _write_audit(session, evaluation.id, "edited", {
        "field": "teacher_confirmed_text",
        "old_length": len(old_text) if old_text else 0,
        "new_length": len(body.teacher_confirmed_text),
    })
    session.commit()
    session.refresh(evaluation)
    return EvaluationRead.model_validate(evaluation)


@router.post("/evaluations/{evaluation_id}/confirm", response_model=EvaluationRead)
def confirm_evaluation(
    evaluation_id: int,
    body: EvaluationConfirmRequest,
    session=Depends(get_session),
):
    """教师确认学生评价。

    保留 AI 原文，追加教师确认文本，不覆盖。
    状态从 draft 转为 confirmed。
    """
    from sqlalchemy import select
    evaluation = session.scalar(
        select(StudentEvaluation).where(StudentEvaluation.id == evaluation_id)
    )
    if evaluation is None:
        raise HTTPException(status_code=404, detail="评价不存在")

    if evaluation.status not in ("draft",):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVALUATION_ALREADY_CONFIRMED",
                "message": f"评价当前状态为 {evaluation.status}，无法重复确认。",
            },
        )

    evaluation.teacher_confirmed_text = body.teacher_confirmed_text
    evaluation.status = "confirmed"
    evaluation.confirmed_at = datetime.now(timezone.utc)
    if body.evidence_snapshot:
        evaluation.evidence_snapshot_json = body.evidence_snapshot
    _write_audit(session, evaluation.id, "confirmed", {
        "teacher_text_length": len(body.teacher_confirmed_text),
    })
    session.commit()
    session.refresh(evaluation)
    return EvaluationRead.model_validate(evaluation)


@router.post("/evaluations/{evaluation_id}/archive", response_model=EvaluationRead)
def archive_evaluation(
    evaluation_id: int, session=Depends(get_session),
):
    """归档已确认的评价。

    状态从 confirmed 转为 archived，不再可编辑或确认。
    """
    from sqlalchemy import select
    evaluation = session.scalar(
        select(StudentEvaluation).where(StudentEvaluation.id == evaluation_id)
    )
    if evaluation is None:
        raise HTTPException(status_code=404, detail="评价不存在")

    if evaluation.status != "confirmed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVALUATION_NOT_CONFIRMED",
                "message": f"评价当前状态为 {evaluation.status}，仅 confirmed 状态可归档。",
            },
        )

    evaluation.status = "archived"
    _write_audit(session, evaluation.id, "archived", {})
    session.commit()
    session.refresh(evaluation)
    return EvaluationRead.model_validate(evaluation)


@router.get("/evaluations/{evaluation_id}/audit", response_model=list[EvaluationAuditEntry])
def get_evaluation_audit(
    evaluation_id: int, session=Depends(get_session),
):
    """获取评价的审计日志。"""
    from sqlalchemy import select
    entries = list(session.scalars(
        select(EvaluationAuditLog)
        .where(EvaluationAuditLog.evaluation_id == evaluation_id)
        .order_by(EvaluationAuditLog.created_at)
    ))
    return [EvaluationAuditEntry.model_validate(e) for e in entries]


# --- 学生画像变更确认 ---

@router.get("/profile-revisions", response_model=list[StudentProfileRevisionRead])
def list_profile_revisions(
    student_id: str | None = Query(default=None),
    term_id: int | None = Query(default=None, gt=0),
    run_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None),
    session=Depends(get_session),
):
    from ..services.student_profiles import list_revisions, revision_payload
    resolved_student_id = None
    if student_id:
        raw = str(student_id).strip()
        student = session.get(Student, int(raw)) if raw.isdigit() else None
        if student is None:
            student = session.scalar(select(Student).where(Student.student_no == raw))
        if student is None:
            return []
        resolved_student_id = student.id
    rows = list_revisions(
        session, student_id=resolved_student_id, term_id=term_id,
        analysis_run_id=run_id, status=status,
    )
    return [StudentProfileRevisionRead.model_validate(revision_payload(row, session)) for row in rows]


@router.post("/profile-revisions", response_model=StudentProfileRevisionRead, status_code=201)
def create_profile_revision(
    body: StudentProfileRevisionCreate,
    session=Depends(get_session),
):
    """创建画像变更草稿；供受控客户端或人工补充使用。"""
    from ..services.student_profiles import create_profile_revision as create_revision, revision_payload
    try:
        revision = create_revision(
            session,
            student_id=body.student_id,
            term_id=body.term_id,
            patch=body.patch,
            analysis_run_id=body.analysis_run_id,
            evidence_ids=body.evidence_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StudentProfileRevisionRead.model_validate(revision_payload(revision, session))


@router.post("/profile-revisions/{revision_id}/confirm", response_model=StudentProfileRevisionRead)
def confirm_profile_revision(
    revision_id: int,
    body: StudentProfileRevisionAction | None = None,
    session=Depends(get_session),
):
    from ..services.student_profiles import confirm_revision, revision_payload
    revision = confirm_revision(session, revision_id, confirmed_by="teacher")
    return StudentProfileRevisionRead.model_validate(revision_payload(revision, session))


@router.post("/profile-revisions/{revision_id}/reject", response_model=StudentProfileRevisionRead)
def reject_profile_revision(
    revision_id: int,
    body: StudentProfileRevisionAction | None = None,
    session=Depends(get_session),
):
    from ..services.student_profiles import reject_revision, revision_payload
    revision = reject_revision(session, revision_id, reason=(body.reason if body else ""))
    return StudentProfileRevisionRead.model_validate(revision_payload(revision, session))


# =====================================================================
# Provider 配置管理
# =====================================================================

# 预定义的可用 provider 模板
_AVAILABLE_PROVIDERS = [
    AvailableProvider(
        id="deepseek",
        display_name="DeepSeek",
        api_format="openai",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        default_api_key_env="DEEPSEEK_API_KEY",
        description="DeepSeek 官方 API，OpenAI 兼容格式，性价比高",
    ),
    AvailableProvider(
        id="anthropic",
        display_name="Anthropic Claude",
        api_format="anthropic",
        default_base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-20250514",
        default_api_key_env="ANTHROPIC_API_KEY",
        description="Anthropic Claude 原生 Messages API，支持视觉和 thinking",
        # P1-5：Harness 运行时暂未接通 Anthropic 原生接口，明确禁用
        supported_in_harness=False,
        unsupported_reason="Harness 运行时暂未支持 Anthropic 原生接口，请使用 DeepSeek 或 OpenAI 标准兼容接口",
    ),
    AvailableProvider(
        id="openai_compat",
        display_name="OpenAI 兼容 (CC Switch / OpenRouter / 等)",
        api_format="openai",
        default_base_url="",
        default_model="gpt-4o",
        default_api_key_env="AGENT_API_KEY",
        description="通用 OpenAI 兼容端点，支持 CC Switch 等代理工具",
    ),
    AvailableProvider(
        id="zhipu",
        display_name="智谱 GLM",
        api_format="openai",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-5.3-flash",
        default_api_key_env="ZHIPU_API_KEY",
        description="智谱官方 OpenAI 兼容接口，支持 thinking 和 image_url 多模态输入",
        supported_in_harness=False,
        unsupported_reason="当前 Harness 运行时是 DeepSeek 专用 SDK；智谱请使用普通 Agent 运行时",
    ),
]

_BUILTIN_MODEL_PROFILE_ID = "builtin-zhipu-glm-4-flash"
_BUILTIN_MODEL_PROFILES = {
    "builtin-zhipu-glm-5-3-flash": {
        "id": "builtin-zhipu-glm-5-3-flash",
        "display_name": "智谱 GLM-5.3-Flash",
        "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model_name": "glm-5.3-flash",
        "api_key_env": "ZHIPU_API_KEY",
        "context_length": 128_000,
        "max_output_tokens": 4_096,
        "supports_tool_calls": True,
        "supports_vision": True,
        "supports_reasoning": True,
        "thinking_enabled": True,
        "reasoning_effort": "high",
        "builtin": True,
    },
    _BUILTIN_MODEL_PROFILE_ID: {
        "id": _BUILTIN_MODEL_PROFILE_ID,
        "display_name": "智谱 GLM-4-Flash",
        "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model_name": "glm-4-flash",
        "api_key_env": "ZHIPU_API_KEY",
        "context_length": 128_000,
        "max_output_tokens": 4_095,
        "supports_tool_calls": True,
        "supports_vision": False,
        "supports_reasoning": False,
        "thinking_enabled": False,
        "reasoning_effort": "off",
        "builtin": True,
    },
}


@router.get("/providers", response_model=list[AvailableProvider])
def list_available_providers():
    """列出所有可用的 provider 类型。"""
    return _AVAILABLE_PROVIDERS


@router.get("/provider", response_model=ProviderInfo)
def get_current_provider():
    """获取当前使用的 provider 配置信息（不含 API Key）。"""
    return ProviderInfo.model_validate(get_provider_info())


def _normalize_model_base_url(value: str, provider: str = "openai_compat") -> str:
    """把根地址、/v1 地址和完整 chat/completions 地址统一为基地址。"""
    url = (value or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")].rstrip("/")
    return url


def _profile_endpoint(base_url: str, provider: str) -> str:
    base = (base_url or "").rstrip("/")
    if provider in {"deepseek", "zhipu"}:
        return base + "/chat/completions"
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


def _profile_key_configured(profile: dict, cfg: AgentConfig) -> bool:
    profile_id = str(profile.get("id") or "")
    if profile_id == "legacy-current":
        return bool(cfg.text_api_key)
    from ..agent.keyvault import load_api_key
    # 当前运行配置与模型档案统一读取同一 profile Key；避免模型列表显示已配置而 Agent 状态显示未配置。
    return bool(load_api_key(profile_id) or os.getenv(profile.get("api_key_env") or ""))


def _model_profiles_response() -> ModelProfilesResponse:
    cfg = get_agent_config()
    stored_profiles = load_model_profiles()
    stored_by_id = {str(item.get("id") or ""): item for item in stored_profiles}
    # 内置模型始终展示；用户保存过它的 API Key 或自定义显示名时，复用已保存档案。
    raw_profiles = []
    for profile_id, builtin in _BUILTIN_MODEL_PROFILES.items():
        saved = stored_by_id.pop(profile_id, None)
        item = dict(builtin)
        if saved:
            item.update(saved)
            item["builtin"] = True
        raw_profiles.append(item)
    raw_profiles.extend(stored_by_id.values())
    # 兼容旧版单模型配置：只有旧配置明确绑定了模型档案 ID、且确实
    # 存在 API Key 时才映射成一个模型档案。只有一个旧版默认 Key、没有
    # 模型档案时，必须返回空列表，不能把默认的 deepseek-chat 当成
    # 用户已经配置的模型展示出来。
    if not stored_profiles and cfg.text_model_profile_id and cfg.text_api_key_configured:
        raw_profiles.append({
            "id": cfg.text_model_profile_id or "legacy-current",
            "display_name": cfg.text_model_name or "当前模型",
            "provider": cfg.text_provider,
            "base_url": cfg.text_api_base_url,
            "endpoint": _profile_endpoint(cfg.text_api_base_url, cfg.text_provider),
            "model_name": cfg.text_model_name,
            "api_key_env": cfg.text_api_key_env,
            "context_length": cfg.text_context_length,
            "max_output_tokens": cfg.text_max_output_tokens,
            "supports_tool_calls": True,
            "supports_vision": False,
            "supports_reasoning": cfg.text_supports_reasoning if cfg.text_provider in {"deepseek", "zhipu"} else False,
            "thinking_enabled": cfg.text_thinking_enabled,
            "reasoning_effort": cfg.text_reasoning_effort,
        })
    models = []
    for item in raw_profiles:
        provider = item.get("provider") or "openai_compat"
        base_url = _normalize_model_base_url(item.get("base_url") or item.get("endpoint") or "", provider)
        endpoint = item.get("endpoint") or _profile_endpoint(base_url, provider)
        if provider == "zhipu":
            from ..agent.keyvault import load_api_key
            from ..agent.providers.zhipu import resolve_zhipu_base_url

            profile_id = str(item.get("id") or "")
            profile_key = load_api_key(profile_id) or os.getenv(item.get("api_key_env") or "") or ""
            resolved_base_url = resolve_zhipu_base_url(base_url, profile_key)
            if resolved_base_url != base_url:
                base_url = resolved_base_url
                endpoint = _profile_endpoint(base_url, provider)
        models.append(ModelProfile(
            id=str(item.get("id") or ""),
            display_name=str(item.get("display_name") or item.get("model_name") or "未命名模型"),
            provider=provider,
            base_url=base_url,
            endpoint=endpoint,
            model_name=str(item.get("model_name") or ""),
            api_key_configured=_profile_key_configured(item, cfg),
            api_key_env=item.get("api_key_env") or "",
            context_length=int(item.get("context_length") or 128_000),
            max_output_tokens=int(item.get("max_output_tokens") or 4_096),
            supports_tool_calls=bool(item.get("supports_tool_calls", True)),
            supports_vision=bool(item.get("supports_vision", False)),
            supports_reasoning=bool(item.get("supports_reasoning", provider in {"deepseek", "zhipu"})),
            thinking_enabled=bool(item.get("thinking_enabled", True)),
            reasoning_effort=str(item.get("reasoning_effort") or "high"),
            builtin=bool(item.get("builtin", False)),
        ))
    current_id = cfg.text_model_profile_id
    if not current_id and models and models[0].id == "legacy-current":
        current_id = "legacy-current"
    return ModelProfilesResponse(models=models, current_id=current_id)


@router.get("/models", response_model=ModelProfilesResponse)
def list_model_profiles():
    """列出本机保存的模型档案（只返回 Key 是否存在）。"""
    return _model_profiles_response()


async def _activate_model_profile(profile: dict) -> tuple[bool, str]:
    """激活档案并复用现有 provider 切换的验证、回滚和重启逻辑。"""
    from dataclasses import replace
    from ..agent.keyvault import load_api_key, save_api_key, clear_api_key

    profile_id = str(profile.get("id") or "")
    profile_key = load_api_key(profile_id) if profile_id != "legacy-current" else load_api_key()
    old_key = load_api_key()
    env_key = os.getenv(profile.get("api_key_env") or "")
    if profile_id != "legacy-current":
        if profile_key:
            save_api_key(profile_key)
        elif not env_key:
            clear_api_key()
    request = ProviderSwitchRequest(
        provider=profile.get("provider") or "openai_compat",
        profile_id=profile_id,
        base_url=profile.get("base_url") or profile.get("endpoint") or "",
        model_name=profile.get("model_name") or "",
        api_key_env=profile.get("api_key_env") or None,
        api_key=profile_key,
        context_length=profile.get("context_length"),
        max_output_tokens=profile.get("max_output_tokens"),
        thinking_enabled=profile.get("thinking_enabled", True),
        reasoning_effort=profile.get("reasoning_effort") or "high",
        supports_reasoning=bool(profile.get("supports_reasoning", False) or profile.get("provider") in {"deepseek", "zhipu"}),
        supports_tool_calls=bool(profile.get("supports_tool_calls", True)),
        supports_vision=bool(profile.get("supports_vision", False)),
    )
    result = await switch_provider(request)
    if not result.success:
        if profile_id != "legacy-current":
            if old_key:
                save_api_key(old_key)
            else:
                clear_api_key()
        return False, result.message
    current = get_agent_config()
    set_runtime_config(replace(current, text_model_profile_id=profile_id), persist=True)
    return True, result.message


@router.post("/models", response_model=ModelProfile)
async def save_model_profile(body: ModelProfileRequest):
    """创建/更新模型档案；保存后默认立即应用。"""
    provider = body.provider or "openai_compat"
    if provider not in {"deepseek", "openai_compat", "zhipu"}:
        raise HTTPException(status_code=400, detail="目前支持 DeepSeek、智谱 GLM 和 OpenAI 兼容接口")
    raw_base_url = body.base_url or body.endpoint
    if provider == "deepseek" and not raw_base_url:
        raw_base_url = "https://api.deepseek.com"
    if provider == "zhipu" and not raw_base_url:
        raw_base_url = "https://open.bigmodel.cn/api/paas/v4"
    base_url = _normalize_model_base_url(raw_base_url, provider)
    if not base_url:
        raise HTTPException(status_code=400, detail="请填写接口地址")
    profile_id = body.id or uuid.uuid4().hex
    endpoint = body.endpoint.strip() if body.endpoint.strip() else _profile_endpoint(base_url, provider)
    if provider == "zhipu":
        from ..agent.keyvault import load_api_key
        from ..agent.providers.zhipu import resolve_zhipu_base_url

        effective_key = body.api_key.strip() if body.api_key else (load_api_key(profile_id) or "")
        resolved_base_url = resolve_zhipu_base_url(base_url, effective_key)
        if resolved_base_url != base_url:
            base_url = resolved_base_url
            endpoint = _profile_endpoint(base_url, provider)
    profiles = load_model_profiles()
    existing = next((p for p in profiles if str(p.get("id")) == profile_id), None)
    if existing is None:
        existing = {"id": profile_id}
        if profile_id in _BUILTIN_MODEL_PROFILES:
            existing["builtin"] = True
        profiles.append(existing)
    existing.update({
        "display_name": body.display_name.strip(),
        "provider": provider,
        "base_url": base_url,
        "endpoint": endpoint,
        "model_name": body.model_name.strip(),
        "api_key_env": body.api_key_env or (
            "DEEPSEEK_API_KEY" if provider == "deepseek"
            else "ZHIPU_API_KEY" if provider == "zhipu"
            else "AGENT_API_KEY"
        ),
        "context_length": body.context_length or 128_000,
        "max_output_tokens": body.max_output_tokens or 4_096,
        "supports_tool_calls": body.supports_tool_calls,
        "supports_vision": body.supports_vision,
        "supports_reasoning": body.supports_reasoning,
        "thinking_enabled": body.thinking_enabled,
        # thinking 关闭时 reasoning_effort 强制 "off"（SDK 方言，防 Harness 崩溃）
        "reasoning_effort": (
            "off"
            if not body.thinking_enabled
            else (body.reasoning_effort if body.reasoning_effort in {"off", "disabled", "low", "medium", "high", "max"} else "high")
        ),
    })
    from ..agent.keyvault import save_api_key, clear_api_key
    if body.api_key is not None:
        if body.api_key.strip():
            save_api_key(body.api_key.strip(), profile_id)
        else:
            clear_api_key(profile_id)
    save_model_profiles(profiles)
    if body.apply:
        ok, message = await _activate_model_profile(existing)
        if not ok:
            raise HTTPException(status_code=400, detail=message)
    return next(item for item in _model_profiles_response().models if item.id == profile_id)


@router.post("/models/{profile_id}/activate", response_model=ProviderSwitchResponse)
async def activate_model_profile(profile_id: str):
    profiles = load_model_profiles()
    profile = next((p for p in profiles if str(p.get("id")) == profile_id), None)
    if profile is None:
        raise HTTPException(status_code=404, detail="模型档案不存在")
    ok, message = await _activate_model_profile(profile)
    return ProviderSwitchResponse(
        success=ok,
        message=message,
        provider_info=ProviderInfo.model_validate(get_provider_info()),
    )


@router.delete("/models/{profile_id}")
def delete_model_profile(profile_id: str):
    if profile_id in _BUILTIN_MODEL_PROFILES:
        raise HTTPException(status_code=400, detail="内置模型不可删除；如需不用可以创建并切换到其他模型")
    profiles = load_model_profiles()
    kept = [p for p in profiles if str(p.get("id")) != profile_id]
    if len(kept) == len(profiles):
        raise HTTPException(status_code=404, detail="模型档案不存在")
    save_model_profiles(kept)
    from ..agent.keyvault import clear_api_key
    clear_api_key(profile_id)
    # 删除当前激活档案时同步清除 runtime 配置中的 profile 引用，
    # 避免后续调用继续读取已删除的保管库 Key。
    if get_agent_config().text_model_profile_id == profile_id:
        from dataclasses import replace
        from ..agent.config import set_runtime_config
        set_runtime_config(replace(get_agent_config(), text_model_profile_id=""), persist=True)
    return {"success": True}


@router.post("/provider/switch", response_model=ProviderSwitchResponse)
async def switch_provider(body: ProviderSwitchRequest):
    """切换 provider（B3-10 增强）。

    - 支持直接在设置页填写 API Key：非空写入本地保管库、空串清除、None 不动；
    - 切换成功后，若 AGENT_RUNTIME=harness，对常驻 Harness 安全重启（apply_config）；
    - 切换失败原子回滚（恢复旧配置与旧 Key 状态）。
    """
    from dataclasses import replace

    # B3-10：Key 变更先行快照（回滚用）
    from ..agent.keyvault import load_api_key, save_api_key, clear_api_key as _clear_key
    from ..agent.runtime.harness_runtime import is_harness_mode
    old_vault_key = load_api_key()

    old_config = get_agent_config()
    old_override = get_runtime_config()

    # 构建新的配置
    # 模型档案 ID 必须与 Provider/URL/Key 同时进入同一个配置快照。
    # 历史实现直到 orchestrator 创建成功后才补写 profile_id，导致
    # AgentConfig.text_api_key 在创建期间优先读取上一个档案的 Key。
    overrides: dict = {
        "text_provider": body.provider,
        "text_model_profile_id": body.profile_id or "",
    }

    # P1-5：Harness 模式暂未接入 Anthropic 原生接口 —— 明确拒绝，不假装可用。
    # （OpenAI 标准兼容接口与 DeepSeek 已真实验证；Anthropic 需后续适配）
    if body.provider == "anthropic" and is_harness_mode():
        return ProviderSwitchResponse(
            success=False,
            message=(
                "Harness 模式暂未支持 Anthropic 原生接口；"
                "请选择 DeepSeek 或 OpenAI 标准兼容接口"
            ),
            provider_info=ProviderInfo.model_validate(
                get_provider_info(old_config)
            ),
        )
    if body.provider == "zhipu" and is_harness_mode():
        return ProviderSwitchResponse(
            success=False,
            message="当前 Harness 运行时是 DeepSeek 专用 SDK；智谱请切换到普通 Agent 运行时后使用",
            provider_info=ProviderInfo.model_validate(get_provider_info(old_config)),
        )

    provider_defaults = {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "anthropic": {
            "base_url": "https://api.anthropic.com",
            "model": "claude-sonnet-4-20250514",
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        "openai_compat": {
            "base_url": "",
            "model": "gpt-4o",
            "api_key_env": "AGENT_API_KEY",
        },
        "zhipu": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-5.3-flash",
            "api_key_env": "ZHIPU_API_KEY",
        },
    }
    defaults = provider_defaults.get(body.provider, {})
    overrides["text_api_base_url"] = _normalize_model_base_url(
        body.base_url or defaults.get("base_url", ""), body.provider
    )
    overrides["text_model_name"] = body.model_name or defaults.get("model", "")
    overrides["text_api_key_env"] = body.api_key_env or defaults.get("api_key_env", "")

    if body.extra_headers:
        overrides["text_extra_headers"] = body.extra_headers
    if body.auth_header:
        overrides["text_auth_header"] = body.auth_header
    if body.auth_prefix:
        overrides["text_auth_prefix"] = body.auth_prefix
    if body.context_length:
        overrides["text_context_length"] = body.context_length
    if body.max_output_tokens:
        overrides["text_max_output_tokens"] = body.max_output_tokens
    if body.thinking_enabled and body.provider == "openai_compat" and body.supports_reasoning is False:
        return ProviderSwitchResponse(
            success=False,
            message="所选中转站模型未声明支持 reasoning_effort；请关闭思考，或开启推理能力声明。",
            provider_info=ProviderInfo.model_validate(get_provider_info(old_config)),
        )
    if body.thinking_enabled is not None:
        overrides["text_thinking_enabled"] = body.thinking_enabled
        # thinking 关闭时 reasoning_effort 强制 "off"（SDK 方言，llm-deepseek
        # 插件只接受 off|high|max；"disabled" 会导致 plugin tree 校验失败崩溃）。
        if not body.thinking_enabled:
            overrides["text_reasoning_effort"] = "off"
    if body.reasoning_effort and overrides.get("text_thinking_enabled", True):
        # DeepSeek V4 官方只接受 high/max；兼容接口保留 low/medium。
        overrides["text_reasoning_effort"] = (
            body.reasoning_effort if body.provider not in {"deepseek", "zhipu"} and body.reasoning_effort in {"low", "medium", "high", "max"}
            else (
                body.reasoning_effort
                if body.provider == "zhipu" and body.reasoning_effort in {"low", "high", "max"}
                else (body.reasoning_effort if body.reasoning_effort in {"high", "max"} else "high")
            )
        )
    if body.supports_reasoning is not None:
        overrides["text_supports_reasoning"] = body.supports_reasoning
    if body.supports_tool_calls is not None:
        overrides["text_supports_tool_calls"] = body.supports_tool_calls
    if body.supports_vision is not None:
        overrides["text_supports_vision"] = body.supports_vision
    if body.supports_vision:
        # 模型档案勾选“图片输入”时，文本和视觉链路共用同一个官方端点与 Key。
        overrides["vision_provider"] = body.provider
        overrides["vision_api_base_url"] = overrides["text_api_base_url"]
        overrides["vision_model_name"] = overrides["text_model_name"]
        overrides["vision_api_key_env"] = overrides["text_api_key_env"]
    elif body.supports_vision is False:
        # 切换到不支持图片的模型时清理旧视觉配置，防止继续误用上一模型。
        overrides["vision_provider"] = ""
        overrides["vision_api_base_url"] = ""
        overrides["vision_model_name"] = ""

    # 设置页的“保存并应用”代表教师已完成模型配置。只有在后续
    # API Key 校验和编排器创建都成功后，才会真正启用 Agent；没有
    # Key 时仍沿用关闭状态，避免把不可用的 Agent 暴露给前端。
    feature_flags = dict(old_config.feature_flags)
    feature_flags["agent_enabled"] = True
    feature_flags["text_agent_enabled"] = True

    new_config = replace(
        old_config,
        config_version=_next_config_version(),
        feature_flags=feature_flags,
        **overrides,
    )

    # B3-10: API Key 写入/清除本地保管库
    from ..agent.keyvault import load_api_key as _load_key
    if body.api_key is not None:
        if body.api_key.strip() == "":
            _clear_key()
        else:
            save_api_key(body.api_key.strip())

    # 检查目标 Provider 的 API Key 是否已配置。直接切换 Provider 时不能复用
    # 其他 Provider 的默认保管库 Key；模型档案激活会把其 profile Key 显式带入 body.api_key。
    target_key = (
        body.api_key.strip()
        if body.api_key is not None and body.api_key.strip()
        else os.getenv(overrides.get("text_api_key_env") or "")
    )
    if body.api_key is None and body.provider == old_config.text_provider:
        target_key = new_config.text_api_key
    if not target_key:
        # 若刚保存了 Key 则回滚（保持一致性）
        if body.api_key is not None and body.api_key.strip():
            if old_vault_key:
                save_api_key(old_vault_key)
            else:
                _clear_key()
        return ProviderSwitchResponse(
            success=False,
            message=f"切换失败：API Key 未配置，请先在设置页填写 API Key",
            provider_info=ProviderInfo.model_validate(get_provider_info(new_config)),
        )

    if body.provider == "zhipu":
        from ..agent.providers.zhipu import resolve_zhipu_base_url

        resolved_base_url = resolve_zhipu_base_url(
            new_config.text_api_base_url, target_key
        )
        if resolved_base_url != new_config.text_api_base_url:
            vision_base_url = new_config.vision_api_base_url
            if new_config.vision_provider == "zhipu":
                vision_base_url = resolve_zhipu_base_url(
                    vision_base_url, target_key
                )
            new_config = replace(
                new_config,
                text_api_base_url=resolved_base_url,
                vision_api_base_url=vision_base_url,
            )

    # 关闭旧编排器，创建新编排器（失败回滚）
    await close_orchestrator()
    try:
        await get_or_create_orchestrator(new_config)
    except Exception as e:
        logger.error("切换 provider 失败: %s", e, exc_info=True)
        if old_override is not None:
            set_runtime_config(old_override)
        else:
            clear_runtime_config()
        # Key 回滚
        if body.api_key is not None and body.api_key.strip():
            if old_vault_key:
                save_api_key(old_vault_key)
            else:
                _clear_key()
        try:
            await get_or_create_orchestrator(old_config)
        except Exception as rollback_err:
            logger.error("回滚旧 provider 也失败: %s", rollback_err, exc_info=True)
        return ProviderSwitchResponse(
            success=False,
            message=f"切换失败：{e}",
            provider_info=ProviderInfo.model_validate(get_provider_info(old_config)),
        )

    set_runtime_config(new_config, persist=True)

    # B3-10: Harness 常驻进程安全重启（热加载新配置；失败不静默）
    harness_restart_notice = ""
    try:
        from ..agent.runtime.harness_runtime import is_harness_mode
        from ..agent.runtime.harness_manager import get_harness_manager
        if is_harness_mode():
            manager = get_harness_manager()
            if manager is not None:
                from ..agent.runtime.harness_runtime import build_harness_config
                from ..config import get_settings
                fresh = build_harness_config(get_settings(), new_config)
                if fresh is not None:
                    restarted = await manager.apply_config(fresh, restart_if_running=True)
                    harness_restart_notice = (
                        "Harness 已重启应用新配置。" if restarted else "Harness 将在下次启动应用新配置。"
                    )
    except Exception as exc:
        harness_restart_notice = f"注意：Harness 配置切换失败（{type(exc).__name__}）；请重启应用。"

    return ProviderSwitchResponse(
        success=True,
        message=f"已切换到 {new_config.provider_display_name}（{new_config.text_model_name}）。"
        + harness_restart_notice,
        provider_info=ProviderInfo.model_validate(get_provider_info(new_config)),
    )


@router.post("/provider/test", response_model=ProviderTestResult)
async def test_provider_connection(body: ProviderTestRequest):
    """测试连接（B3-10）：只验证可达性与鉴权，不保存模型回答。

    Key 优先使用请求体（若提供），否则当前配置（保管库 > 环境变量）。
    """
    import time as _time

    from ..agent.config import get_agent_config

    cfg = get_agent_config()
    provider = body.provider or cfg.text_provider
    base_url = body.base_url or ("https://api.deepseek.com" if provider == "deepseek" else cfg.text_api_base_url)
    model_name = body.model_name or cfg.text_model_name
    if body.api_key is not None:
        api_key = body.api_key
    elif body.profile_id:
        from ..agent.keyvault import load_api_key
        api_key = load_api_key(body.profile_id)
    else:
        api_key = cfg.text_api_key

    if not api_key:
        return ProviderTestResult(ok=False, message="API Key 未配置", provider=provider,
                                  model_name=model_name)
    if not base_url:
        return ProviderTestResult(ok=False, message="API Base URL 未配置", provider=provider,
                                  model_name=model_name)

    if provider == "zhipu":
        # BigModel 与 Z.AI 是两套凭据域。历史版本把中国开放平台的
        # id.secret Key 发到 api.z.ai，导致所有真实 Key 都被误报为鉴权失败。
        from ..agent.providers.zhipu import resolve_zhipu_base_url
        base_url = resolve_zhipu_base_url(base_url, api_key)

    started = _time.monotonic()
    try:
        import httpx
        normalized_base = _normalize_model_base_url(base_url, provider)
        if provider in {"deepseek", "zhipu"}:
            url = f"{normalized_base}/chat/completions"
        elif provider == "anthropic":
            url = f"{normalized_base}{'/messages' if normalized_base.endswith('/v1') else '/v1/messages'}"
        else:
            url = f"{normalized_base}{'/chat/completions' if normalized_base.endswith('/v1') else '/v1/chat/completions'}"
        if provider == "anthropic":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
        else:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"model": model_name, "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 1}
        thinking_enabled = body.thinking_enabled if body.thinking_enabled is not None else True
        if provider == "deepseek":
            payload["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
            if thinking_enabled:
                payload["reasoning_effort"] = body.reasoning_effort if body.reasoning_effort in {"high", "max"} else "high"
        elif provider == "zhipu":
            from ..agent.providers.zhipu import (
                zhipu_model_is_glm53,
                zhipu_model_supports_thinking,
            )
            if zhipu_model_supports_thinking(model_name):
                if thinking_enabled:
                    payload["thinking"] = {"type": "enabled"}
                    if zhipu_model_is_glm53(model_name):
                        payload["reasoning_effort"] = (
                            body.reasoning_effort
                            if body.reasoning_effort in {"low", "high", "max"}
                            else "high"
                        )
        elif provider != "anthropic" and body.reasoning_effort:
            payload["reasoning_effort"] = body.reasoning_effort
        async with httpx.AsyncClient(timeout=15.0) as client:
            if body.send_chat_request:
                resp = await client.post(url, json=payload, headers=headers)
            else:
                # 仅做连通性探测时不消耗模型额度；GET /models 是 OpenAI
                # 兼容端点的标准探针，Anthropic 没有统一 models API，退回
                # 基地址 GET，只把 5xx/网络错误视为不可达。
                probe_url = (
                    f"{normalized_base}/models"
                    if provider in {"deepseek", "zhipu"}
                    else f"{normalized_base}{'/models' if normalized_base.endswith('/v1') else '/v1/models'}"
                )
                resp = await client.get(probe_url, headers=headers)
        latency_ms = int((_time.monotonic() - started) * 1000)
        if not body.send_chat_request and resp.status_code < 500:
            return ProviderTestResult(
                ok=resp.status_code < 400,
                latency_ms=latency_ms,
                message=("服务地址可达（未发送模型消息）" if resp.status_code < 400
                         else f"服务地址可达，但探针返回 HTTP {resp.status_code}（未发送模型消息）"),
                provider=provider, model_name=model_name,
            )
        if resp.status_code < 400:
            return ProviderTestResult(ok=True, latency_ms=latency_ms,
                                      message="连接成功（测试消息未保存）",
                                      provider=provider, model_name=model_name)
        # 402 = 账户余额不足（Key 有效但无法计费），给出可读提示。
        if resp.status_code == 402:
            return ProviderTestResult(
                ok=False, latency_ms=latency_ms,
                message="连接可达但余额不足（HTTP 402）：API Key 有效，请前往服务商平台充值后重试",
                provider=provider, model_name=model_name,
            )
        return ProviderTestResult(
            ok=False, latency_ms=latency_ms,
            message=f"连接失败 HTTP {resp.status_code}：{resp.text[:200]}",
            provider=provider, model_name=model_name,
        )
    except Exception as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        return ProviderTestResult(ok=False, latency_ms=latency_ms,
                                 message=f"连接失败：{type(exc).__name__}: {exc}",
                                 provider=provider, model_name=model_name)


@router.get("/provider/status", response_model=ProviderRuntimeStatus)
async def provider_runtime_status():
    """Harness/运行时运行状态（B3-10；不含 API Key）。"""
    from ..agent.runtime.harness_runtime import is_harness_mode
    from ..agent.runtime.harness_manager import get_harness_manager
    from ..agent.config import get_agent_config
    import os as _os

    cfg = get_agent_config()
    mode = "harness" if is_harness_mode() else ("harness-http" if _os.getenv(
        "AGENT_RUNTIME", "legacy").strip().lower() == "harness-http" else "legacy")

    pid = restart = last_error = None
    running = False
    queue_depth = 0
    active_method = None
    runtime_generation = 0
    pool_size = 1
    active_slots = 0
    manager = get_harness_manager()
    if manager is not None:
        health = manager.health()
        running = health.running
        pid = getattr(health, "pid", None)
        restart = getattr(health, "restart_count", 0)
        last_error = getattr(health, "last_error", None)
        queue_depth = getattr(health, "queue_depth", 0)
        active_method = getattr(health, "active_method", None)
        runtime_generation = getattr(health, "generation", 0)
        pool_size = getattr(health, "pool_size", 1)
        active_slots = getattr(health, "active_slots", 0)

    from ..agent.keyvault import api_key_configured
    return ProviderRuntimeStatus(
        mode=mode,
        configured=cfg.text_api_key_configured,
        running=running,
        pid=pid,
        restart_count=restart or 0,
        last_error=last_error,
        provider=cfg.text_provider,
        model_name=cfg.text_model_name,
        config_version=cfg.config_version,
        key_configured=api_key_configured(),
        queue_depth=queue_depth,
        active_method=active_method,
        runtime_generation=runtime_generation,
        pool_size=pool_size,
        active_slots=active_slots,
    )
