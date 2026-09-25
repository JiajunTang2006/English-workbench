"""视觉分析服务层（方案 §10.4 状态机 / §10.5 Provider 不直接写库）。

职责：
- 驱动状态机：uploaded → parsing → pending_ocr → vision_running →
  pending_review → (confirmed / rejected / failed)
- 调用 Provider（经 registry）写 analysis_result；
- 幂等：同一 (attachment_id, provider, result_type) 去重；
- 审计：request_id / 费用 / 确认版本；
- 隐私：不向普通日志写图像 Base64 / OCR 原文 / 完整 Provider 响应；
- 门禁：confirmed 前结果不可进入正式上下文（由 FormalContext 复用确认状态）；
- 防提示注入：文档内恶意提示不改变工具权限（仅作为普通文本处理）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.entities import (
    Attachment,
    AttachmentAnalysisResult,
    AttachmentDerivative,
)
from .provider import VisionTask, result_to_content_json
from .registry import get_provider

logger = logging.getLogger(__name__)

# 合法状态集合（用于状态机校验）
VALID_STATUS = {
    "queued",
    "running",
    "pending_review",
    "confirmed",
    "rejected",
    "failed",
}

# 不可被普通内容直接推进到终态的起始（避免越权）
_TERMINAL = {"confirmed", "rejected", "failed"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VisionAnalysisService:
    def __init__(self, db: Session, *, data_dir: Path | None = None):
        self._db = db
        self._data_dir = Path(data_dir) if data_dir else None

    # --- 查询 ---

    def list_results(self, attachment_id: int) -> list[dict[str, Any]]:
        rows = self._db.execute(
            select(AttachmentAnalysisResult)
            .where(AttachmentAnalysisResult.attachment_id == attachment_id)
            .order_by(AttachmentAnalysisResult.id)
        ).scalars().all()
        return [_public_result(r) for r in rows]

    def get_result(self, result_id: int) -> dict[str, Any] | None:
        r = self._db.get(AttachmentAnalysisResult, result_id)
        return _public_result(r) if r else None

    def get_confirmed_content(self, attachment_id: int) -> str | None:
        """仅返回已 confirmed 的视觉结果正文（供正式上下文读取）。

        未确认或被拒/失败均返回 None —— 教师确认前不进入正式上下文。
        """
        row = self._db.execute(
            select(AttachmentAnalysisResult)
            .where(
                AttachmentAnalysisResult.attachment_id == attachment_id,
                AttachmentAnalysisResult.status == "confirmed",
            )
            .order_by(AttachmentAnalysisResult.confirmed_at.desc())
        ).scalars().first()
        if not row:
            return None
        pages = (row.content_json or {}).get("pages") or []
        return "\n".join(str(p.get("text", "")) for p in pages if p.get("text"))

    # --- 触发分析 ---

    async def analyze_attachment(
        self,
        *,
        attachment_id: int,
        task: VisionTask,
        result_type: str,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """触发一次视觉分析（状态机：queued → running → pending_review）。

        幂等：同 (attachment, provider, result_type) 已存在 running/pending_review
        时直接返回既有记录，不重复调用。
        """
        attachment = self._db.get(Attachment, attachment_id)
        if attachment is None:
            raise ValueError("attachment_not_found")

        provider = get_provider(provider_name) or get_provider()
        if provider is None:
            raise RuntimeError("no_vision_provider_available")

        # 幂等：查找既有未完成记录
        existing = self._db.execute(
            select(AttachmentAnalysisResult).where(
                AttachmentAnalysisResult.attachment_id == attachment_id,
                AttachmentAnalysisResult.provider == provider.capabilities.name,
                AttachmentAnalysisResult.result_type == result_type,
                AttachmentAnalysisResult.status.in_(["queued", "running", "pending_review"]),
            )
        ).scalars().first()
        if existing is not None:
            return _public_result(existing)

        result = AttachmentAnalysisResult(
            attachment_id=attachment_id,
            provider=provider.capabilities.name,
            model_name=provider.capabilities.name,
            result_type=result_type,
            status="running",
        )
        self._db.add(result)
        self._db.flush()

        # 调用 Provider（不写库，仅返回 VisionResult）
        image_inputs = self._build_image_inputs(attachment)
        vision = await provider.analyze(
            images=image_inputs,
            task=task,
            response_schema={"type": "object", "properties": {}},
        )

        result.status = "pending_review"  # 需教师校对
        result.content_json = result_to_content_json(vision)
        result.cost_yuan = vision.cost_yuan
        result.provider_request_id = vision.request_id
        result.model_name = vision.model_name
        if vision.error:
            result.status = "failed"
            result.error_message = vision.error
        self._db.commit()
        return _public_result(result)

    # --- 教师校对 / 确认 / 拒绝 ---

    def teacher_review(
        self,
        *,
        result_id: int,
        action: str,  # confirm / reject
        correction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """教师确认或拒绝。confirm 进入终态并记录确认版本。"""
        if action not in {"confirm", "reject"}:
            raise ValueError("invalid_action")
        r = self._db.get(AttachmentAnalysisResult, result_id)
        if r is None:
            raise ValueError("result_not_found")
        if r.status in _TERMINAL:
            # 已终态：仅允许在 confirmed 上追加修正（重新确认）
            if action == "confirm" and correction:
                r.teacher_correction_json = correction
                r.content_json = {**r.content_json, "teacher_corrected": True,
                                  "corrected_pages": correction.get("pages", [])}
                r.confirmed_at = utcnow()
                self._db.commit()
                return _public_result(r)
            raise ValueError("already_terminal")

        if action == "confirm":
            r.status = "confirmed"
            r.confirmed_at = utcnow()
            if correction:
                r.teacher_correction_json = correction
                r.content_json = {**r.content_json, "teacher_corrected": True,
                                  "corrected_pages": correction.get("pages", [])}
        else:
            r.status = "rejected"
        self._db.commit()
        # 审计：不记原文，只记状态迁移与版本
        logger.info(
            "vision_result_reviewed result_id=%s action=%s status=%s",
            result_id, action, r.status,
        )
        return _public_result(r)

    # --- 内部 ---

    def _build_image_inputs(self, attachment: Attachment) -> list:
        """根据派生图构建 ImageInput（无派生图时留空，由 Provider 占位处理）。

        不在此读取/外发图像 Base64；仅传递本地路径引用。
        """
        from .provider import ImageInput

        derivatives = self._db.execute(
            select(AttachmentDerivative).where(
                AttachmentDerivative.attachment_id == attachment.id,
                AttachmentDerivative.kind.in_(["page_image", "thumbnail"]),
            )
        ).scalars().all()
        inputs = []
        for d in derivatives:
            if not d.storage_name:
                continue
            # Provider receives the private local path; public result and logs
            # never expose it.  Derivative paths are confined to attachments/.
            if self._data_dir is None:
                continue
            from ..attachment_security import safe_storage_path
            try:
                path = safe_storage_path(self._data_dir / "attachments", d.storage_name)
            except (ValueError, OSError):
                continue
            inputs.append(ImageInput(path=str(path), page_no=d.page_no,
                                     width=d.width, height=d.height))
        if not inputs:
            # 图片附件在上传后可能尚未生成派生图；使用原始文件作为输入，
            # 但仍限定在受控附件目录内。
            if self._data_dir is not None:
                from ..attachment_security import safe_storage_path
                try:
                    path = safe_storage_path(self._data_dir / "attachments", attachment.storage_name)
                    inputs.append(ImageInput(path=str(path), page_no=1))
                except (ValueError, OSError):
                    pass
        return inputs


def _public_result(r: AttachmentAnalysisResult) -> dict[str, Any]:
    """对外结果（不含教师修正原文冗余、不含图像路径）。"""
    cj = r.content_json or {}
    return {
        "id": r.id,
        "attachment_id": r.attachment_id,
        "provider": r.provider,
        "model_name": r.model_name,
        "result_type": r.result_type,
        "status": r.status,
        "pages": cj.get("pages", []),
        "low_confidence": cj.get("low_confidence", []),
        "cost_yuan": r.cost_yuan,
        "request_id": r.provider_request_id,
        "has_correction": r.teacher_correction_json is not None,
        "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        "error": r.error_message,
    }
