"""会话与消息管理服务。

负责 AgentSession 的 CRUD、消息持久化、多轮上下文构建。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...models.agent_entities import (
    AgentSession,
    AgentMessage,
    AgentMessageAttachment,
)

logger = logging.getLogger(__name__)

# 多轮上下文最多携带的原始历史消息条数。更早消息只通过滚动摘要进入模型。
MAX_CONTEXT_MESSAGES = 6
# 自动摘要只保存对连续交流有用的短摘录，不把旧的整段回答重新塞回 prompt。
AUTO_SUMMARY_PREFIX = "[自动滚动摘要 v1]"
AUTO_SUMMARY_MAX_TOKENS = 1_000
AUTO_SUMMARY_MAX_USER_CHARS = 180
AUTO_SUMMARY_MAX_ASSISTANT_CHARS = 220


class SessionService:
    """Agent 会话管理服务。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_session(
        self,
        *,
        title: str = "新会话",
        term_id: int,
        class_id: int | None = None,
        exam_id: int | None = None,
        student_id: int | None = None,
    ) -> AgentSession:
        """创建新的 Agent 会话。

        作用域通过 class_id/exam_id/student_id 独立列存储。
        """
        session = AgentSession(
            title=title,
            term_id=term_id,
            class_id=class_id,
            exam_id=exam_id,
            student_id=student_id,
            status="active",
        )
        self._db.add(session)
        self._db.commit()
        self._db.refresh(session)
        logger.info("创建 Agent 会话: id=%d, term=%d", session.id, term_id)
        return session

    def get_session(self, session_id: int) -> AgentSession | None:
        """获取会话（排除已软删除）。"""
        return self._db.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.deleted_at.is_(None),
            )
        )

    def list_sessions(
        self,
        *,
        term_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentSession]:
        """分页列出会话。"""
        stmt = select(AgentSession).where(AgentSession.deleted_at.is_(None))
        if term_id is not None:
            stmt = stmt.where(AgentSession.term_id == term_id)
        stmt = stmt.order_by(AgentSession.updated_at.desc()).limit(limit).offset(offset)
        return list(self._db.scalars(stmt))

    def update_session(
        self,
        session_id: int,
        *,
        title: str | None = None,
        summary: str | None = None,
        status: str | None = None,
    ) -> AgentSession | None:
        """更新会话标题、摘要或状态。"""
        session = self.get_session(session_id)
        if session is None:
            return None
        if title is not None:
            session.title = title
        if summary is not None:
            session.summary = summary
        if status is not None:
            session.status = status
        self._db.commit()
        self._db.refresh(session)
        return session

    def soft_delete(self, session_id: int) -> bool:
        """软删除会话到回收站。"""
        session = self.get_session(session_id)
        if session is None:
            return False
        session.deleted_at = datetime.now(timezone.utc)
        self._db.commit()
        return True

    def restore_session(self, session_id: int) -> AgentSession | None:
        """恢复软删除的会话。"""
        session = self._db.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.deleted_at.is_not(None),
            )
        )
        if session is None:
            return None
        session.deleted_at = None
        session.status = "active"
        self._db.commit()
        self._db.refresh(session)
        return session

    # ------------------------------------------------------------------
    # 消息持久化
    # ------------------------------------------------------------------

    def add_message(
        self,
        *,
        session_id: int,
        role: str,
        content_text: str | None = None,
        structured_answer: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        model_name: str | None = None,
        provider_request_id: str | None = None,
    ) -> AgentMessage:
        """向会话添加消息（用户/助手/系统）。

        助手消息可携带结构化答案和证据 ID。
        """
        msg = AgentMessage(
            session_id=session_id,
            role=role,
            content_text=content_text,
            structured_answer_json=structured_answer or {},
            evidence_ids_json=evidence_ids or [],
            model_name=model_name,
            provider_request_id=provider_request_id,
        )
        self._db.add(msg)
        # 会话列表按 updated_at 排序；新增消息必须把当前会话提升到最近活动，
        # 否则教师刚刚对话的会话会继续沉在历史列表底部。
        session = self._db.get(AgentSession, session_id)
        if session is not None:
            session.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(msg)
        return msg

    def list_messages(
        self,
        session_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentMessage]:
        """分页列出会话消息（按时间排序）。"""
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list(self._db.scalars(stmt))

    def get_scope_from_session(self, session: AgentSession) -> dict[str, int]:
        """从会话记录提取作用域字典。"""
        scope: dict[str, int] = {}
        if session.exam_id:
            scope["exam_id"] = session.exam_id
        if session.class_id:
            scope["class_id"] = session.class_id
        if session.student_id:
            scope["student_id"] = session.student_id
        return scope

    def build_multi_turn_messages(
        self,
        session_id: int,
        system_prompt: str,
        user_message: str,
        *,
        privacy_mapper: Any = None,
        current_run_id: int | None = None,
        formal_context_token_budget: int | None = None,
        history_token_budget: int | None = None,
        include_formal_context: bool = True,
    ) -> list[dict[str, str]]:
        """构建多轮上下文消息列表。

        包含：
        - 系统规则
        - 当前固定作用域的安全摘要
        - 脱敏会话摘要（如存在）
        - 最近 MAX_CONTEXT_MESSAGES 条消息
        - 本轮新消息

        历史原始工具结果不无限重复发送。
        """
        # 系统消息
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

        # 正式资料（B2-02：唯一读取门禁 FormalContextProvider，教师已确认附件）
        # 注入前必须脱敏：正式资料正文可能含真实姓名/学校，发往 Provider 前只能保留匿名副本
        # B3-04：必须传入真实 data_dir —— 附件文件丢失时禁止引用（文件存在校验）
        if include_formal_context:
            try:
                from .formal_context import build_formal_context
                from ...config import get_settings
                data_dir = get_settings().data_dir
                formal_text = build_formal_context(
                    self._db, session_id, data_dir=data_dir,
                    max_tokens=formal_context_token_budget,
                )
                if formal_text:
                    if privacy_mapper:
                        formal_text = privacy_mapper.sanitize_text(formal_text)
                    messages.append({
                        "role": "system",
                        "content": f"教师已确认的正式资料（仅可引用，不可篡改）：\n{formal_text}",
                    })
            except Exception:
                logger.warning("构建正式资料上下文失败（会话 %s）", session_id, exc_info=True)

        # 会话摘要：超过最近窗口的消息先在本地压成短摘要，再作为系统上下文注入。
        # 摘要不是事实证据；成绩等事实仍必须来自数据库工具或已确认资料。
        self.refresh_session_summary(
            session_id,
            current_run_id=current_run_id,
        )
        session = self.get_session(session_id)
        if session and session.summary:
            summary_text = session.summary
            if privacy_mapper:
                summary_text = privacy_mapper.sanitize_text(summary_text)
            messages.append({
                "role": "system",
                "content": f"会话摘要：{summary_text}",
            })

        # 历史消息（最近 N 条）
        # 取“最近”消息而非会话最早消息；当前 run 的 user 消息已在发送前落库，
        # 必须排除，否则本轮 user_message 会被重复注入一次。
        stmt = select(AgentMessage).where(AgentMessage.session_id == session_id)
        if current_run_id is not None:
            stmt = stmt.where(or_(
                AgentMessage.analysis_run_id.is_(None),
                AgentMessage.analysis_run_id != current_run_id,
            ))
        latest = list(self._db.scalars(
            stmt.order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(MAX_CONTEXT_MESSAGES)
        ))
        history = list(reversed(latest))
        from ...agent.token_budget import estimate_text_tokens
        rendered_history: list[tuple[dict[str, str], int]] = []
        for msg in history:
            if msg.role == "user":
                content = msg.content_text or ""
                if privacy_mapper:
                    content = privacy_mapper.sanitize_text(content)
                token_count = estimate_text_tokens(content)
                rendered_history.append(({"role": "user", "content": content}, token_count))
            elif msg.role == "assistant":
                content = msg.content_text or ""
                if privacy_mapper:
                    # 历史 assistant 消息（模型此前生成的原文）同样必须脱敏后才可回发 Provider
                    content = privacy_mapper.sanitize_text(content)
                # 如有结构化答案，附加简要描述
                if msg.structured_answer_json:
                    summary = msg.structured_answer_json.get("summary", "")
                    if summary:
                        if privacy_mapper:
                            summary = privacy_mapper.sanitize_text(summary)
                        # 结构化报告正文通常很长；历史轮只回传其自带摘要，完整
                        # 报告仍保存在数据库并由当前界面展示。
                        content = f"[上一轮助手摘要] {summary}"
                token_count = estimate_text_tokens(content)
                rendered_history.append(({"role": "assistant", "content": content}, token_count))

        # 从最新消息向前消费预算，避免一条较长的旧回答挤掉真正相关的最近消息。
        history_used = 0
        selected_history: list[dict[str, str]] = []
        for item, token_count in reversed(rendered_history):
            if history_token_budget is not None and history_used + token_count > history_token_budget:
                continue
            selected_history.append(item)
            history_used += token_count
        messages.extend(reversed(selected_history))

        # 本轮新消息
        current_content = user_message
        if privacy_mapper:
            current_content = privacy_mapper.sanitize_text(current_content)
        messages.append({"role": "user", "content": current_content})

        return messages

    def refresh_session_summary(
        self,
        session_id: int,
        *,
        current_run_id: int | None = None,
    ) -> str:
        """本地生成滚动会话摘要，并保存到 ``AgentSession.summary``。

        该摘要不调用模型，因此不会为省 token 再产生一次模型费用。只摘要已经
        滑出最近消息窗口的内容；助手回答优先使用结构化答案自带的短 summary，
        并把总摘要控制在固定预算内。

        人工写入的摘要会保留在自动摘要之前；自动部分每次从数据库重建，避免
        反复摘要摘要导致内容漂移或重复。
        """
        session = self.get_session(session_id)
        if session is None:
            return ""

        stmt = select(AgentMessage).where(
            AgentMessage.session_id == session_id,
            AgentMessage.role.in_(("user", "assistant")),
        )
        if current_run_id is not None:
            stmt = stmt.where(or_(
                AgentMessage.analysis_run_id.is_(None),
                AgentMessage.analysis_run_id != current_run_id,
            ))
        rows = list(self._db.scalars(
            stmt.order_by(AgentMessage.created_at, AgentMessage.id)
        ))

        existing = session.summary or ""
        manual_summary = existing.split(AUTO_SUMMARY_PREFIX, 1)[0].strip()
        older = rows[:-MAX_CONTEXT_MESSAGES]
        if not older:
            return existing

        from ...agent.token_budget import estimate_text_tokens

        candidates: list[str] = []
        for msg in older:
            if msg.role == "user":
                excerpt = self._compact_summary_text(
                    msg.content_text or "", AUTO_SUMMARY_MAX_USER_CHARS,
                )
                label = "教师"
            else:
                structured = msg.structured_answer_json or {}
                excerpt = self._compact_summary_text(
                    str(structured.get("summary") or msg.content_text or ""),
                    AUTO_SUMMARY_MAX_ASSISTANT_CHARS,
                )
                label = "助手草稿"
            if excerpt:
                candidates.append(f"- {label}：{excerpt}")

        header = (
            f"{AUTO_SUMMARY_PREFIX}\n"
            "较早对话要点（仅用于保持任务连续，不作为成绩或学生事实证据）："
        )
        selected: list[str] = []
        # 优先保留离当前任务最近的旧消息；最后再恢复时间顺序。
        for line in reversed(candidates):
            proposed = [line, *selected]
            body = "\n".join([header, *proposed])
            if estimate_text_tokens(body) > AUTO_SUMMARY_MAX_TOKENS:
                continue
            selected = proposed
        auto_summary = "\n".join([header, *selected])
        combined = "\n\n".join(part for part in (manual_summary, auto_summary) if part)
        if combined != existing:
            session.summary = combined
            self._db.flush()
        return combined

    @staticmethod
    def _compact_summary_text(text: str, max_chars: int) -> str:
        """把单条消息压成适合本地滚动摘要的一行。"""
        compact = " ".join(str(text).split())
        if len(compact) <= max_chars:
            return compact
        return compact[:max_chars].rstrip() + "…"

    def save_session_summary(self, session_id: int, summary: str) -> None:
        """保存脱敏会话摘要。

        未经教师确认的 AI 诊断只能作为"上一轮 AI 草稿"，不能作为事实证据。
        """
        session = self.get_session(session_id)
        if session:
            session.summary = summary
            self._db.commit()
