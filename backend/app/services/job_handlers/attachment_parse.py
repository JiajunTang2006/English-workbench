"""附件解析任务处理器 (U4-02 / S0-05)

将上传的附件在后台解析，提取文本内容。
解析结果写入 attachment 的 metadata_json，在教师确认前不进入正式考试事实表。

工作流：
  上传 → 文件校验 → 后台解析（本处理器）→ 教师校对 → 晋升为正式资料 → Agent 可引用

S0-05 状态语义：
  - 成功解析:      附件 pending_review，任务 completed
  - 图片等待 OCR:  附件 pending_ocr，任务 waiting_ocr（明确待处理，非完全成功）
  - 文件损坏/不支持:附件 parse_failed，任务 failed（抛 PermanentJobError，不可重试）
  - 临时错误/超时:  抛 RetryableJobError，进入指数退避队列，超过最大尝试后 failed
  - 用户取消:      附件/任务 cancelled，不会被后续覆盖为 completed

错误信息同时写入附件 metadata（parsed.error）与后台任务 last_error；
不把敏感附件内容写入日志。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...models.agent_entities import BackgroundJob
from ...models.entities import Attachment
from ...services.job_worker import PermanentJobError, RetryableJobError
from ..attachment_security import safe_storage_path, validate_stored_file

logger = logging.getLogger(__name__)

# 判断永久性失败的文本标记（顺序无关，命中即不可重试）
PERMANENT_ERROR_MARKERS = (
    "不支持", "文件不存在", "损坏", "校验和", "sha256", "hash 不匹配",
    "格式", "pdfplumber", "PDFSyntaxError", "不是 PDF", "不是XLSX",
)
TIMEOUT_MARKER = "解析超时"


def _is_permanent_parse_error(result: dict[str, Any], error: str) -> bool:
    """根据解析结果与错误文本判断是否为不可恢复错误。"""
    if result.get("format") == "unknown":
        return True  # 不支持的格式
    if error == TIMEOUT_MARKER:
        return False  # 超时属于临时错误，允许有限重试
    lowered = error.lower()
    return any(marker.lower() in lowered for marker in PERMANENT_ERROR_MARKERS)


def _write_parse_metadata(
    attachment: Attachment,
    result: dict[str, Any],
    *,
    status: str,
    job_id: int | None,
    purpose: str,
    error: str | None = None,
) -> None:
    """把解析结果写入附件 metadata（成功或失败均调用）。"""
    metadata = dict(attachment.metadata_json or {})
    metadata["parsed"] = {
        "format": result.get("format", "unknown"),
        "content": result.get("content", ""),
        "pages": result.get("pages", []),
        "parse_metadata": result.get("metadata", {}),
        "needs_ocr": result.get("needs_ocr", False),
        "error": error,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "parsed_by_job": job_id,
        "purpose": purpose,
        "status": status,
    }
    attachment.metadata_json = metadata


def attachment_parse_handler(
    job: BackgroundJob,
    session: Session,
    checkpoint: dict[str, Any],
    is_cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """解析附件文件，提取文本内容。

    scope_json 需要:
        - attachment_id: 附件 ID
        - settings_data_dir: 数据目录路径（用于定位附件文件）
        - purpose: 附件用途 (exam_paper/answer_key/item_score_sheet/...)

    解析结果写入 attachment.metadata_json["parsed"]，
    包含 content, format, pages, metadata 等字段。
    """
    from ..document_parser import DocumentParser

    attachment_id = job.scope_json.get("attachment_id")
    if attachment_id is None:
        raise PermanentJobError("attachment_parse 需要 scope.attachment_id")

    data_dir = job.scope_json.get("settings_data_dir")
    if data_dir is None:
        raise PermanentJobError("attachment_parse 需要 scope.settings_data_dir")

    purpose = job.scope_json.get("purpose", "chat_context")

    # 获取附件记录
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise PermanentJobError(f"附件 #{attachment_id} 不存在")

    # 构建文件路径并做安全校验
    attachments_dir = Path(data_dir) / "attachments"
    try:
        file_path = safe_storage_path(attachments_dir, attachment.storage_name)
        validate_stored_file(
            file_path,
            expected_size=attachment.size_bytes,
            expected_sha256=attachment.sha256,
        )
    except (ValueError, FileNotFoundError) as exc:
        # 文件缺失或校验不匹配 = 损坏/被篡改，不可恢复
        _write_parse_metadata(
            attachment, {"format": "unknown", "content": "", "pages": [], "metadata": {}, "needs_ocr": False},
            status="parse_failed", job_id=job.id, purpose=purpose, error=str(exc),
        )
        session.commit()
        raise PermanentJobError(f"附件文件损坏或不可用: {exc}") from exc

    if is_cancelled():
        checkpoint["cancelled"] = True
        return checkpoint

    # 解析文档
    parser = DocumentParser()
    result = parser.parse(file_path)
    if result.get("format") == "pdf" and result.get("needs_ocr") and not result.get("error"):
        from ..pdf_ocr import complete_scanned_pdf
        result = complete_scanned_pdf(file_path, result, should_cancel=is_cancelled)

    if is_cancelled():
        checkpoint["cancelled"] = True
        return checkpoint

    parse_error = result.get("error")

    if parse_error is not None:
        # 先持久化失败状态到附件 metadata，再抛出对应语义异常
        _write_parse_metadata(
            attachment, result,
            status="parse_failed", job_id=job.id, purpose=purpose,
            error=parse_error,
        )
        session.commit()
        if _is_permanent_parse_error(result, parse_error):
            raise PermanentJobError(f"附件 #{attachment_id} 解析失败（不可重试）: {parse_error}")
        raise RetryableJobError(f"附件 #{attachment_id} 解析临时失败: {parse_error}")

    # 图片或扫描 PDF 等待 OCR；已提取的 PDF 页面仍保留在 parsed.pages。
    if result.get("needs_ocr"):
        _write_parse_metadata(
            attachment, result,
            status="pending_ocr", job_id=job.id, purpose=purpose,
            error=None,
        )
        checkpoint["attachment_id"] = attachment_id
        checkpoint["format"] = result.get("format")
        checkpoint["needs_ocr"] = True
        checkpoint["status"] = "pending_ocr"
        logger.info("附件 #%d 等待 OCR（format=%s）", attachment_id, result.get("format"))
        return checkpoint

    # 成功解析：附件 pending_review，任务 completed
    _write_parse_metadata(
        attachment, result,
        status="pending_review", job_id=job.id, purpose=purpose,
        error=None,
    )

    # 更新进度
    job.progress = 1.0

    checkpoint["attachment_id"] = attachment_id
    checkpoint["format"] = result.get("format")
    checkpoint["needs_ocr"] = False
    checkpoint["error"] = None
    checkpoint["status"] = "pending_review"

    logger.info(
        "附件 #%d 解析完成 (format=%s, status=pending_review, needs_ocr=False)",
        attachment_id,
        result.get("format"),
    )
    return checkpoint
