"""正式资料上下文提供者 (B2-02)

附件晋升为正式资料后，只有通过本服务才能进入 Agent 的正式上下文。
禁止其他模块直接绕过本服务读取附件解析正文。

进入正式上下文必须同时满足（全条件）：
  1. 附件属于当前 session（通过 AgentMessage -> AgentMessageAttachment 关联）；
  2. 附件属于当前 term（Attachment.term_id == AgentSession.term_id）；
  3. 存在有效的消息-附件关联记录；
  4. 关联记录 promoted_to_formal = True；
  5. 附件解析状态为 confirmed（metadata_json.parsed.status == "confirmed"）；
  6. 会话未被软删除（deleted_at IS NULL 且 status == "active"）；
  7. 附件文件仍存在，且解析结果（正文/错误）可读。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.agent_entities import AgentMessage, AgentMessageAttachment, AgentSession

logger = logging.getLogger(__name__)


class FormalContextProvider:
    """正式资料读取门禁（唯一入口）。"""

    def __init__(self, db: Session, *, data_dir: Path | None = None):
        self._db = db
        self._data_dir = Path(data_dir) if data_dir else None

    # --- 查询 API ---

    def list_formal_attachments(self, session_id: int) -> list[dict[str, Any]]:
        """返回当前会话可引用的正式资料元数据（不含正文，供工具列表用）。

        返回空列表表示没有可引用资料。任何一项条件不满足即排除。
        """
        session = self._db.get(AgentSession, session_id)
        if session is None:
            return []
        if session.deleted_at is not None or session.status != "active":
            # 会话已软删除或非活跃，正式资料不可引用
            return []
        if session.term_id is None:
            return []

        rows = self._db.execute(
            select(AgentMessageAttachment)
            .join(AgentMessage, AgentMessageAttachment.message_id == AgentMessage.id)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessageAttachment.attachment_id.is_not(None),
                AgentMessageAttachment.promoted_to_formal.is_(True),
            )
            .order_by(AgentMessageAttachment.id)
        ).scalars().all()

        result: list[dict[str, Any]] = []
        seen_attachment_ids: set[int] = set()
        from ...models.entities import Attachment

        for ma in rows:
            attachment = self._db.get(Attachment, ma.attachment_id)
            if attachment is None:
                continue
            if attachment.id in seen_attachment_ids:
                continue
            if attachment.term_id != session.term_id:
                continue  # 跨学期不可见
            parsed = (attachment.metadata_json or {}).get("parsed") or {}
            if parsed.get("status") != "confirmed":
                continue
            if parsed.get("error"):
                continue
            content = parsed.get("content") or ""
            if not content.strip():
                continue
            if not self._attachment_file_exists(attachment.storage_name):
                continue
            seen_attachment_ids.add(attachment.id)
            result.append({
                "attachment_id": attachment.id,
                "title": attachment.title or attachment.original_name,
                "original_name": attachment.original_name,
                "purpose": ma.purpose,
                "confirmed_at": parsed.get("confirmed_at"),
            })
        return result

    def get_formal_context_text(self, session_id: int, *, max_chars: int = 8000,
                                max_tokens: int | None = None,
                                max_attachments: int = 10) -> str | None:
        """拼接正式资料正文（脱敏交由上层，正文本身来自已确认解析结果）。

        作为 Agent 上文的注入文本；无可用资料时返回 None。
        max_attachments 限制单次注入的附件数（B3-07/07 数量上限）。
        """
        metadata_list = self.list_formal_attachments(session_id)
        if not metadata_list:
            return None

        from ...models.entities import Attachment

        from ...agent.token_budget import estimate_text_tokens, truncate_text_to_tokens

        parts: list[str] = []
        total = 0
        total_tokens = 0
        attached = 0
        for index, meta in enumerate(metadata_list):
            if attached >= max_attachments:
                break
            attachment = self._db.get(Attachment, meta["attachment_id"])
            if attachment is None:
                continue
            parsed = (attachment.metadata_json or {}).get("parsed") or {}
            content = (parsed.get("content") or "").strip()
            if not content:
                continue
            head = f"【{meta['title']}】（教师已确认正式资料，purpose={meta['purpose']}）"
            remaining_attachments = max(
                1, min(max_attachments - attached, len(metadata_list) - index)
            )
            char_room = max(
                0, (max_chars - total) // remaining_attachments - len(head)
            )
            token_room = None
            if max_tokens is not None:
                token_room = (
                    (max_tokens - total_tokens) // remaining_attachments
                    - estimate_text_tokens(head)
                )
                if token_room <= 0:
                    break
                char_room = min(char_room, max(64, token_room * 4))
            body, _note = smart_truncate_parsed(parsed, max_chars=char_room)
            if token_room is not None:
                body = truncate_text_to_tokens(body, token_room)
            if not body.strip():
                continue
            parts.append(f"{head}\n{body}")
            total += len(head) + len(body)
            total_tokens += estimate_text_tokens(parts[-1])
            attached += 1
            if total >= max_chars or (max_tokens is not None and total_tokens >= max_tokens):
                break

        if not parts:
            return None
        return "\n\n".join(parts)

    # --- 插件只读视图（无会话，按 term 聚合已确认资料；L2）---

    def _is_confirmed_text(self, attachment: "object") -> tuple[bool, dict | None]:
        """复用与 list_formal_attachments 完全相同的门禁：

        parsed.status == "confirmed" 且 无 error 且 正文非空 且 文件存在。
        返回 (是否可引用, parsed)。
        """
        parsed = (attachment.metadata_json or {}).get("parsed") or {}
        if parsed.get("status") != "confirmed":
            return False, None
        if parsed.get("error"):
            return False, None
        content = parsed.get("content") or ""
        if not content.strip():
            return False, None
        if not self._attachment_file_exists(attachment.storage_name):
            return False, None
        return True, parsed

    def list_confirmed_materials(self, term_id: int) -> list[dict[str, object]]:
        """返回本学期全部可引用的已确认资料元数据（不含正文）。

        跨学期不可见；未确认/解析错误/文件丢失的附件一律排除。
        """
        from ...models.entities import Attachment

        rows = self._db.scalars(
            select(Attachment).where(Attachment.term_id == term_id)
        ).all()
        result: list[dict[str, object]] = []
        for attachment in rows:
            ok, parsed = self._is_confirmed_text(attachment)
            if not ok or parsed is None:
                continue
            content = parsed.get("content") or ""
            result.append({
                "material_id": attachment.id,
                "title": attachment.title or attachment.original_name,
                "original_name": attachment.original_name,
                "purpose": None,
                "confirmed_at": parsed.get("confirmed_at"),
                "char_count": len(content),
            })
        return result

    def read_confirmed_material(self, attachment_id: int, term_id: int, *,
                                page: int = 1, page_size: int = 4000) -> dict | None:
        """分页读取单个已确认资料正文（1-based page）。

        越权（跨学期/未确认/文件丢失）返回 None；不暴露未确认正文。
        """
        from ...models.entities import Attachment

        attachment = self._db.get(Attachment, attachment_id)
        if attachment is None or attachment.term_id != term_id:
            return None
        ok, parsed = self._is_confirmed_text(attachment)
        if not ok or parsed is None:
            return None
        content = parsed.get("content") or ""
        total = len(content)
        page = max(1, page)
        start = (page - 1) * page_size
        end = min(total, start + page_size)
        text = content[start:end]
        total_pages = max(1, (total + page_size - 1) // page_size)
        return {
            "material_id": attachment.id,
            "title": attachment.title or attachment.original_name,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_chars": total,
            "text": text,
            "truncated": end < total or start > 0,
        }

    # --- 内部 ---

    def _attachment_file_exists(self, storage_name: str) -> bool:
        """附件文件仍然存在。data_dir 未知时按通过校验处理（DB 门禁为主）。"""
        if self._data_dir is None:
            return True
        try:
            from ...services.attachment_security import safe_storage_path
            path = safe_storage_path(self._data_dir / "attachments", storage_name)
        except Exception:
            return False
        return path.is_file()


# 页级保留策略（B2 智能裁剪）：首页 + 表格页 + 末页 + 中间每 _PDF_SAMPLE_STEP 页抽 1 页
_PDF_SAMPLE_STEP = 5
_PDF_PAGE_MAX_CHARS = 1200


def smart_truncate_parsed(parsed: dict, *, max_chars: int = 1500) -> tuple[str, str]:
    """正式资料正文智能裁剪（B2，替代简单前截断）。

    - PDF：按页保留（首页/表格页/末页/抽样页），省略的连续页合并标注
      `[第 X~Y 页已省略]`，让模型知道资料不完整、不得编造；
    - 其他格式：前截断 + 字符省略标注。
    返回 (裁剪文本, 读取说明)。
    """
    fmt = parsed.get("format") or ""
    content = (parsed.get("content") or "").strip()
    if not content:
        return "", ""
    pages = parsed.get("pages") if isinstance(parsed.get("pages"), list) else []
    if fmt == "pdf" and len(pages) > 1:
        total = len(pages)
        keep = {0, total - 1}
        for i, pg in enumerate(pages):
            if pg.get("table_count"):
                keep.add(i)
        for i in range(0, total, _PDF_SAMPLE_STEP):
            keep.add(i)
        parts: list[str] = []
        used = 0
        omitted_pages: list[int] = []
        for i in range(total):
            text = (pages[i].get("text") or "").strip()
            if not text:
                continue
            if i not in keep:
                omitted_pages.append(i + 1)
                continue
            head = f"【第{i + 1}页】"
            room = max_chars - used - len(head) - 2
            if room <= 0:
                break
            body = text[:_PDF_PAGE_MAX_CHARS]
            if len(body) > room:
                body = body[:room] + "…"
            parts.append(f"{head}\n{body}")
            used += len(parts[-1])
        # 合并省略页标注
        note = ""
        if omitted_pages:
            ranges: list[str] = []
            start = prev = omitted_pages[0]
            for p in omitted_pages[1:]:
                if p == prev + 1:
                    prev = p
                else:
                    ranges.append(f"{start}" if start == prev else f"{start}~{prev}")
                    start = prev = p
            ranges.append(f"{start}" if start == prev else f"{start}~{prev}")
            note = f"[第{'、'.join(ranges)}页已省略：纯文本题目正文；如需可调用正式附件读取工具按页查看]"
        result = "\n\n".join(parts)
        if note:
            result += "\n\n" + note
        return result, note
    # 非 PDF：前截断 + 标注
    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n[已省略 {len(content) - max_chars} 字；如需可调用正式附件读取工具分段查看]", \
            f"[已省略 {len(content) - max_chars} 字]"
    return content, ""


def build_formal_context(
    db: Session,
    session_id: int,
    *,
    data_dir: Path | None = None,
    max_chars: int = 8000,
    max_tokens: int | None = None,
    max_attachments: int = 10,
) -> str | None:
    """便捷入口：构建正式资料上下文文本（无资料返回 None）。

    :param data_dir: 数据目录（用于校验附件文件真实存在；缺失时按存在处理）。
                     生产路径必须传入真实 data_dir —— 文件丢失的附件不得进入上下文。
    :param max_chars: 注入正文总上限（B2：20000→8000 省输入 token；长 PDF 走智能裁剪 + 按页工具）。
    :param max_attachments: 正文最多引用附件数（B3-07 字符/页数/数量上限）。
    """
    provider = FormalContextProvider(db, data_dir=data_dir)
    return provider.get_formal_context_text(session_id, max_chars=max_chars,
                                            max_tokens=max_tokens,
                                            max_attachments=max_attachments)
