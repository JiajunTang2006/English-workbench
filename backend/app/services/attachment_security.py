"""附件安全与资源限制 (U4-03)。

上传入口和后台解析任务共用这些校验，避免只在 HTTP 层检查而被内部任务绕过。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from PIL import Image

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_PDF_PAGES = 200

ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".md": "text/markdown",
}


def safe_storage_path(root: str | Path, storage_name: str) -> Path:
    """Resolve an attachment path and reject absolute paths/traversal."""
    root_path = Path(root).resolve()
    candidate = Path(storage_name)
    if candidate.name != storage_name or candidate.is_absolute():
        raise ValueError("附件路径无效")
    target = (root_path / candidate).resolve()
    if root_path not in target.parents:
        raise ValueError("附件路径无效")
    return target


def _validate_office_zip(raw: bytes, *, required_members: set[str], label: str) -> None:
    """校验 Open XML Office 包，拒绝只有 ZIP 魔数但缺少正文结构的伪文件。"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = set(archive.namelist())
            if not required_members.issubset(names):
                raise ValueError(f"文件内容不是有效 {label}")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"文件内容不是有效 {label}")
    except ValueError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError(f"文件内容不是有效 {label}") from exc


def _validate_office_zip_path(path: Path, *, required_members: set[str], label: str) -> None:
    """从路径校验 Open XML Office 包，供流式上传落盘门禁使用。"""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not required_members.issubset(names):
                raise ValueError(f"文件内容不是有效 {label}")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"文件内容不是有效 {label}")
    except ValueError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError(f"文件内容不是有效 {label}") from exc


def _validate_signature(suffix: str, raw: bytes) -> None:
    if suffix == ".pdf" and not raw.startswith(b"%PDF-"):
        raise ValueError("文件内容不是有效 PDF")
    if suffix == ".png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("文件内容不是有效 PNG")
    if suffix in {".jpg", ".jpeg"} and not raw.startswith(b"\xff\xd8\xff"):
        raise ValueError("文件内容不是有效 JPEG")
    if suffix == ".xlsx":
        if not raw.startswith(b"PK\x03\x04"):
            raise ValueError("文件内容不是有效 Excel")
        _validate_office_zip(
            raw,
            required_members={"[Content_Types].xml", "xl/workbook.xml"},
            label="Excel",
        )
    if suffix == ".xls" and not raw.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("文件内容不是有效 Excel")
    if suffix == ".docx":
        if not raw.startswith(b"PK\x03\x04"):
            raise ValueError("文件内容不是有效 Word DOCX")
        _validate_office_zip(
            raw,
            required_members={"[Content_Types].xml", "word/document.xml"},
            label="Word DOCX",
        )
    if suffix == ".doc" and not raw.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("文件内容不是有效 Word DOC")


def validate_upload(name: str, declared_mime: str, raw: bytes) -> tuple[str, str, dict[str, Any]]:
    """校验上传内容并返回安全文件名、检测 MIME 和元数据。"""
    if not raw:
        raise ValueError("附件不能为空")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise ValueError("单个附件不能超过50MB")
    safe_name = Path(name).name
    suffix = Path(safe_name).suffix.lower()
    expected_mime = ALLOWED_EXTENSIONS.get(suffix)
    if expected_mime is None:
        raise ValueError("不支持的附件格式")
    declared = (declared_mime or "application/octet-stream").lower()
    if declared != "application/octet-stream" and declared != expected_mime:
        raise ValueError("文件扩展名与 MIME 类型不一致")

    _validate_signature(suffix, raw)
    metadata: dict[str, Any] = {}
    if suffix in {".png", ".jpg", ".jpeg"}:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素数超过限制")
                image.verify()
                metadata.update(width=width, height=height, image_format=image.format)
        except ValueError:
            raise
        except Exception as exc:
            # Preserve the pixel-limit boundary even when a test/provider supplies
            # a lightweight image object rather than a fully decodable bitmap.
            try:
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素数超过限制") from exc
            except ValueError:
                raise
            except Exception:
                pass
            raise ValueError("图片内容无效") from exc
    if suffix == ".pdf":
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                if len(pdf.pages) > MAX_PDF_PAGES:
                    raise ValueError("PDF 页数超过限制")
                metadata.update(total_pages=len(pdf.pages))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("文件内容不是可解析的 PDF") from exc
    return safe_name, expected_mime, metadata


def validate_stored_file(path: str | Path, *, expected_size: int, expected_sha256: str) -> bytes:
    """读取已存储附件并校验大小、哈希和格式签名。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"附件文件不存在: {file_path.name}")
    size = file_path.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError("附件大小超过限制")
    if expected_size != size:
        raise ValueError("附件大小校验失败")
    raw = file_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("附件 SHA-256 校验失败")
    suffix = file_path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持的附件格式")
    _validate_signature(suffix, raw)
    if suffix in {".png", ".jpg", ".jpeg"}:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素数超过限制")
            image.verify()
    return raw


def as_http_error(error: ValueError) -> HTTPException:
    message = str(error)
    status = 413 if any(word in message for word in ("超过", "大小")) else 415
    return HTTPException(status, message)


def base64_content_size(encoded: str) -> int:
    """按 Base64 编码长度估算原始字节数（解码前使用，B2-04）。

    len(encoded)*3//4 为上限估算（含 padding/换行时更保守）。
    """
    return (len(encoded) * 3) // 4


def validate_stored_path(
    root: str | Path,
    storage_name: str,
    *,
    expected_size: int,
    expected_sha256: str,
    expected_mime: str | None = None,
    expected_name: str | None = None,
) -> None:
    """流式写入落盘后的最终校验。

    校验存在性、大小、SHA-256、扩展名/MIME 一致性与魔数，并对图片/PDF
    从路径流式解码（不读全量正文进内存）校验像素/页数限制。
    用于 multipart 流式上传在原子移动前的门禁（B2-04）。

    :param expected_name: 临时文件名可能不携带真实扩展名（如 .tmp），
        传入原始文件名以 .name 推导后缀与 MIME。
    失败时抛出 ``ValueError``（大小超限/格式不匹配），由调用方转 HTTP。
    """
    path = safe_storage_path(root, storage_name)
    if not path.is_file():
        raise FileNotFoundError(f"附件文件不存在: {path.name}")
    size = path.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError("单个附件不能超过50MB")
    if expected_size != size:
        raise ValueError("附件大小校验失败")
    with path.open("rb") as fh:
        head = fh.read(4096)
        digest = hashlib.sha256()
        digest.update(head)
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        raise ValueError("附件 SHA-256 校验失败")
    suffix = Path(expected_name or storage_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持的附件格式")
    expected_mime = expected_mime or ALLOWED_EXTENSIONS.get(suffix)
    # 流式落盘校验只把前缀 ``head`` 交给魔数检查；Office ZIP 的完整
    # 目录结构已在下面通过 ``_validate_office_zip_path`` 校验，不能把
    # 4096 字节前缀当成完整 ZIP 再调用 ``_validate_signature``。
    if suffix in {".xlsx", ".docx"}:
        if not head.startswith(b"PK\x03\x04"):
            label = "Excel" if suffix == ".xlsx" else "Word DOCX"
            raise ValueError(f"文件内容不是有效 {label}")
    else:
        _validate_signature(suffix, head)
    if suffix == ".xlsx":
        _validate_office_zip_path(
            path,
            required_members={"[Content_Types].xml", "xl/workbook.xml"},
            label="Excel",
        )
    if suffix == ".docx":
        _validate_office_zip_path(
            path,
            required_members={"[Content_Types].xml", "word/document.xml"},
            label="Word DOCX",
        )
    if suffix == ".doc" and not head.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("文件内容不是有效 Word DOC")

    # 图片：从路径流式解码（不整读），校验像素上限
    if suffix in {".png", ".jpg", ".jpeg"}:
        try:
            with Image.open(path) as image:
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素数超过限制")
                image.verify()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("图片内容无效") from exc

    # PDF：从路径打开并限制页数（pdfplumber 支持路径句柄）
    if suffix == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages) > MAX_PDF_PAGES:
                    raise ValueError("PDF 页数超过限制")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("文件内容不是可解析的 PDF") from exc
