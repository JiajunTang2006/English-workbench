"""附件上传能力、错误码、并发与指标（L1 / AttachmentUploadAPI v1）。

本模块是长期开发方案 L1 阶段的后端补强层，职责：

- 对外声明上传能力与限制，使前端不再硬编码 15MB 之类的魔法值；
- 把 ``attachment_security`` 抛出的 ``ValueError`` 统一映射为带稳定
  ``code`` 的 HTTP 错误，供前端做可本地化的分支处理；
- 清理历史遗留的 ``.upload-*.tmp`` 临时文件（应用启动时执行）；
- 限制并发上传数量，避免多个大文件同时写盘打满磁盘与内存；
- 记录上传指标（大小、耗时、状态），**不记录任何文件正文**。

设计边界：
- 不做业务校验（学期归属、去重）——那仍属路由与服务层；
- 不解析文件内容——安全校验仍由 ``attachment_security`` 负责；
- 指标只在内存聚合，不写数据库、不落日志正文。
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException

from .attachment_security import (
    ALLOWED_EXTENSIONS,
    MAX_ATTACHMENT_BYTES,
    MAX_IMAGE_PIXELS,
    MAX_PDF_PAGES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 协议版本（AttachmentUploadAPI v1）
# 只新增兼容字段时保持小版本；删除或重解释字段必须升大版本。
# ---------------------------------------------------------------------------

ATTACHMENT_UPLOAD_API_VERSION = "1.0"

# 临时文件命名约定（与 routers/attachments.py 的 multipart 写入保持一致）
TMP_UPLOAD_PREFIX = ".upload-"
TMP_UPLOAD_SUFFIX = ".tmp"
TMP_UPLOAD_GLOB = f"{TMP_UPLOAD_PREFIX}*{TMP_UPLOAD_SUFFIX}"

# 流式读取分块大小（与路由一致，用于前端展示与进度粒度提示）
UPLOAD_CHUNK_BYTES = 1 << 20

# 并发上传上限：同时写盘的大文件数量。超出返回 429，前端应排队重试。
DEFAULT_MAX_CONCURRENT_UPLOADS = 3

# 启动清理阈值：早于该秒数的临时文件视为上次异常退出的残留。
# 应用启动时目录内不可能有正在进行的上传，故默认 0（全部清理）。
STALE_TMP_AGE_SECONDS = 0


# ---------------------------------------------------------------------------
# 统一错误码
# ---------------------------------------------------------------------------

class UploadErrorCode:
    """上传失败的稳定错误码（前端按 code 分支，不解析中文文案）。"""

    EMPTY_FILE = "UPLOAD_EMPTY_FILE"
    TOO_LARGE = "UPLOAD_TOO_LARGE"
    UNSUPPORTED_TYPE = "UPLOAD_UNSUPPORTED_TYPE"
    MIME_MISMATCH = "UPLOAD_MIME_MISMATCH"
    SIGNATURE_MISMATCH = "UPLOAD_SIGNATURE_MISMATCH"
    IMAGE_TOO_LARGE = "UPLOAD_IMAGE_TOO_LARGE"
    IMAGE_INVALID = "UPLOAD_IMAGE_INVALID"
    PDF_TOO_MANY_PAGES = "UPLOAD_PDF_TOO_MANY_PAGES"
    INTEGRITY_FAILED = "UPLOAD_INTEGRITY_FAILED"
    INVALID_PATH = "UPLOAD_INVALID_PATH"
    BUSY = "UPLOAD_BUSY"
    STORAGE_FAILED = "UPLOAD_STORAGE_FAILED"


# (匹配片段, 错误码, HTTP 状态)
# 顺序敏感：更具体的片段必须排在更宽泛的片段之前。
_ERROR_RULES: tuple[tuple[str, str, int], ...] = (
    ("附件不能为空", UploadErrorCode.EMPTY_FILE, 422),
    ("图片像素数超过限制", UploadErrorCode.IMAGE_TOO_LARGE, 413),
    ("图片内容无效", UploadErrorCode.IMAGE_INVALID, 415),
    ("PDF 页数超过限制", UploadErrorCode.PDF_TOO_MANY_PAGES, 413),
    ("不能超过", UploadErrorCode.TOO_LARGE, 413),
    ("大小超过限制", UploadErrorCode.TOO_LARGE, 413),
    ("不支持的附件格式", UploadErrorCode.UNSUPPORTED_TYPE, 415),
    ("扩展名与 MIME", UploadErrorCode.MIME_MISMATCH, 415),
    ("SHA-256 校验失败", UploadErrorCode.INTEGRITY_FAILED, 400),
    ("大小校验失败", UploadErrorCode.INTEGRITY_FAILED, 400),
    ("路径无效", UploadErrorCode.INVALID_PATH, 400),
    # 兜底：所有 "文件内容不是有效/可解析的 X" 归为签名不匹配
    ("文件内容不是", UploadErrorCode.SIGNATURE_MISMATCH, 415),
)


def classify_upload_error(message: str) -> tuple[str, int]:
    """把校验失败文案映射为 ``(code, http_status)``。

    未命中规则时回退 415（不支持的内容），避免把校验失败误报成 500。
    """
    for fragment, code, status in _ERROR_RULES:
        if fragment in message:
            return code, status
    return UploadErrorCode.UNSUPPORTED_TYPE, 415


def upload_http_error(error: ValueError | str, *, code: str | None = None,
                      status: int | None = None) -> HTTPException:
    """构造带稳定 ``code`` 的上传错误响应。

    响应体形如 ``{"detail": {"code": "UPLOAD_TOO_LARGE", "message": "..."}}``，
    与 agent 路由既有的错误结构一致，前端 ``apiRequest`` 已能读取 ``detail.code``。
    """
    message = str(error)
    resolved_code, resolved_status = classify_upload_error(message)
    return HTTPException(
        status_code=status or resolved_status,
        detail={"code": code or resolved_code, "message": message},
    )


# ---------------------------------------------------------------------------
# 能力声明
# ---------------------------------------------------------------------------

def upload_capabilities(*, multipart_enabled: bool = True,
                        max_concurrent: int = DEFAULT_MAX_CONCURRENT_UPLOADS) -> dict:
    """返回上传能力与限制（AttachmentUploadAPI v1）。

    前端必须从此接口读取限制，不得硬编码。返回内容不含任何绝对路径、
    Token 或存储细节。
    """
    extensions = sorted(ALLOWED_EXTENSIONS)
    image_extensions = sorted(
        ext for ext, mime in ALLOWED_EXTENSIONS.items() if mime.startswith("image/")
    )
    return {
        "api_version": ATTACHMENT_UPLOAD_API_VERSION,
        "multipart_enabled": bool(multipart_enabled),
        "multipart_endpoint": "/api/v1/attachments/upload",
        "legacy_base64_endpoint": "/api/v1/attachments",
        "max_attachment_bytes": MAX_ATTACHMENT_BYTES,
        "max_image_pixels": MAX_IMAGE_PIXELS,
        "max_pdf_pages": MAX_PDF_PAGES,
        "max_concurrent_uploads": max_concurrent,
        "chunk_size_bytes": UPLOAD_CHUNK_BYTES,
        "allowed_extensions": extensions,
        "image_extensions": image_extensions,
        "accept_attribute": ",".join(extensions),
        "extension_mime_map": dict(sorted(ALLOWED_EXTENSIONS.items())),
        "supports_cancel": True,
        "supports_progress": True,
        "dedupe_scope": "term_id+sha256",
        "error_codes": sorted(
            value for key, value in vars(UploadErrorCode).items()
            if not key.startswith("_") and isinstance(value, str)
        ),
    }


# ---------------------------------------------------------------------------
# 临时文件清理
# ---------------------------------------------------------------------------

def cleanup_stale_upload_temp_files(
    attachments_dir: str | Path,
    *,
    max_age_seconds: float = STALE_TMP_AGE_SECONDS,
) -> dict[str, int]:
    """清理残留的 ``.upload-*.tmp`` 临时文件。

    上传路由自身在 ``finally`` 中清理临时文件，但进程被强杀（掉电、
    ``kill -9``、打包应用崩溃）时不会执行。应用启动时调用本函数补齐。

    :param max_age_seconds: 只清理修改时间早于该秒数的文件；0 表示全部清理。
    :return: ``{"removed": n, "failed": n, "bytes": n}``
    """
    directory = Path(attachments_dir)
    stats = {"removed": 0, "failed": 0, "bytes": 0}
    if not directory.is_dir():
        return stats
    now = time.time()
    for path in directory.glob(TMP_UPLOAD_GLOB):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
            if max_age_seconds > 0 and (now - stat.st_mtime) < max_age_seconds:
                continue
            size = stat.st_size
            path.unlink()
            stats["removed"] += 1
            stats["bytes"] += size
        except OSError:
            stats["failed"] += 1
    if stats["removed"] or stats["failed"]:
        logger.info(
            "附件上传临时文件清理：removed=%d failed=%d bytes=%d",
            stats["removed"], stats["failed"], stats["bytes"],
        )
    return stats


# ---------------------------------------------------------------------------
# 并发上限
# ---------------------------------------------------------------------------

_active_uploads = 0


def active_upload_count() -> int:
    """当前正在写盘的上传数（测试与诊断用）。"""
    return _active_uploads


@contextmanager
def upload_slot(max_concurrent: int = DEFAULT_MAX_CONCURRENT_UPLOADS) -> Iterator[None]:
    """占用一个并发上传名额，超限抛 429。

    FastAPI 的 async 端点在单一事件循环中运行，``await`` 之间不会被抢占，
    因此普通计数器足够，无需额外锁。
    """
    global _active_uploads
    if _active_uploads >= max_concurrent:
        raise HTTPException(
            status_code=429,
            detail={
                "code": UploadErrorCode.BUSY,
                "message": f"同时上传的文件过多（上限 {max_concurrent}），请稍后重试",
                "max_concurrent_uploads": max_concurrent,
            },
        )
    _active_uploads += 1
    try:
        yield
    finally:
        _active_uploads -= 1


def reset_upload_slots() -> None:
    """重置并发计数（仅供测试隔离使用）。"""
    global _active_uploads
    _active_uploads = 0


# ---------------------------------------------------------------------------
# 上传指标（大小、耗时、状态；不记录正文）
# ---------------------------------------------------------------------------

@dataclass
class UploadMetrics:
    """进程内上传指标聚合。

    只保存计数与字节数等标量，不保存文件名、正文或哈希，避免日志泄漏。
    """

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    deduped: int = 0
    total_bytes: int = 0
    total_duration_ms: int = 0
    by_code: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict:
        average = int(self.total_duration_ms / self.total) if self.total else 0
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "deduped": self.deduped,
            "total_bytes": self.total_bytes,
            "average_duration_ms": average,
            "by_code": dict(self.by_code),
        }

    def reset(self) -> None:
        self.total = 0
        self.succeeded = 0
        self.failed = 0
        self.deduped = 0
        self.total_bytes = 0
        self.total_duration_ms = 0
        self.by_code.clear()


_metrics = UploadMetrics()


def upload_metrics_snapshot() -> dict:
    """返回上传指标快照。"""
    return _metrics.snapshot()


def reset_upload_metrics() -> None:
    """重置指标（仅供测试隔离使用）。"""
    _metrics.reset()


def record_upload_metric(
    *,
    status: str,
    size_bytes: int,
    duration_ms: int,
    code: str | None = None,
    extension: str = "",
) -> None:
    """记录一次上传结果。

    :param status: ``succeeded`` / ``deduped`` / ``failed``
    :param code: 失败错误码（成功时为 None）
    :param extension: 文件扩展名（用于分布统计，不含文件名本体）
    """
    _metrics.total += 1
    _metrics.total_bytes += max(0, int(size_bytes))
    _metrics.total_duration_ms += max(0, int(duration_ms))
    if status == "succeeded":
        _metrics.succeeded += 1
    elif status == "deduped":
        _metrics.deduped += 1
    else:
        _metrics.failed += 1
    if code:
        _metrics.by_code[code] = _metrics.by_code.get(code, 0) + 1
    # 只记录标量维度：状态、扩展名、字节数、耗时、错误码。不记录文件名与正文。
    logger.info(
        "attachment_upload status=%s ext=%s bytes=%d duration_ms=%d code=%s",
        status, extension or "-", size_bytes, duration_ms, code or "-",
    )
