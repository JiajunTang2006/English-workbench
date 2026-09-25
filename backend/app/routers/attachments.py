from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update

from ..auth import require_token
from ..models import Attachment, AgentMessageAttachment, ChangeLog, StudentItemResult, WorkspaceState
from ..schemas import AttachmentCreate, AttachmentRead, AttachmentWorkspaceDelete, AttachmentWorkspaceDeleteRead
from ..services.attachment_security import (
    ALLOWED_EXTENSIONS,
    MAX_ATTACHMENT_BYTES,
    as_http_error,
    base64_content_size,
    safe_storage_path,
    validate_stored_path,
    validate_upload,
)
from ..services.attachment_uploads import (
    DEFAULT_MAX_CONCURRENT_UPLOADS,
    TMP_UPLOAD_PREFIX,
    TMP_UPLOAD_SUFFIX,
    UploadErrorCode,
    record_upload_metric,
    upload_capabilities,
    upload_http_error,
    upload_metrics_snapshot,
    upload_slot,
)
from ..services.file_operations import enqueue_file_operation, process_pending_file_operations
from ..services.terms import current_term_id, require_term

router = APIRouter(prefix="/api/v1/attachments", tags=["attachments"], dependencies=[Depends(require_token)])
def _session(request: Request):
    return request.app.state.session_factory()


def _safe_path(settings, storage_name: str) -> Path:
    try:
        return safe_storage_path(settings.attachments_dir, storage_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/capabilities")
def get_upload_capabilities():
    """返回上传能力与限制（AttachmentUploadAPI v1）。

    前端必须从此接口读取大小/格式限制，不得硬编码。``multipart_enabled``
    反映 L1 功能开关，关闭时前端应回退到 Base64 兼容入口。
    """
    from ..agent.config import get_agent_config

    try:
        # L1 多部件上传是前端上传机制，独立于 Agent 总开关：直接读取原始
        # 功能标志，避免 ``is_feature_enabled`` 的 agent_enabled 总闸误杀。
        enabled = bool(get_agent_config().feature_flags.get("multipart_ui_enabled", False))
    except Exception:  # 配置异常不能让上传能力查询失败
        enabled = False
    payload = upload_capabilities(
        multipart_enabled=enabled,
        max_concurrent=DEFAULT_MAX_CONCURRENT_UPLOADS,
    )
    payload["metrics"] = upload_metrics_snapshot()
    return payload


@router.get("", response_model=list[AttachmentRead])
def list_attachments(request: Request, term_id: int | None = None):
    with _session(request) as session:
        selected_id = term_id or current_term_id(session)
        require_term(session, selected_id)
        return list(session.scalars(select(Attachment).where(Attachment.term_id == selected_id).order_by(Attachment.created_at.desc(), Attachment.id.desc())))


@router.post("", response_model=AttachmentRead, status_code=201)
def create_attachment(payload: AttachmentCreate, request: Request, term_id: int | None = None):
    with _session(request) as session:
        selected_id = term_id or current_term_id(session)
        require_term(session, selected_id, active_only=True)
        # B2-04: 解码前按编码长度估算大小，超限立即拒绝（不完整解码）
        if base64_content_size(payload.content_base64) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "单个附件不能超过50MB")
        try:
            raw = base64.b64decode(payload.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise HTTPException(422, "附件内容不是有效的 base64") from error
        try:
            safe_name, detected_mime, upload_metadata = validate_upload(
                payload.original_name, payload.mime_type.strip() or "application/octet-stream", raw,
            )
        except ValueError as exc:
            raise as_http_error(exc) from exc
        digest = hashlib.sha256(raw).hexdigest()
        existing = session.scalar(
            select(Attachment).where(
                Attachment.term_id == selected_id,
                Attachment.sha256 == digest,
            )
        )
        if existing is not None:
            return existing
        suffix = Path(safe_name).suffix.lower()
        storage_name = f"term_{selected_id}_{secrets.token_hex(16)}{suffix}"
        path = _safe_path(request.app.state.settings, storage_name)
        path.write_bytes(raw)
        metadata = dict(payload.metadata or {})
        if upload_metadata:
            metadata.setdefault("security", {}).update(upload_metadata)
        item = Attachment(term_id=selected_id, title=payload.title.strip(), original_name=safe_name, mime_type=detected_mime, size_bytes=len(raw), sha256=digest, storage_name=storage_name, metadata_json=metadata)
        session.add(item)
        try:
            session.commit()
        except Exception:
            session.rollback()
            path.unlink(missing_ok=True)
            raise
        session.refresh(item)
        return item


@router.post("/upload", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    term_id: int | None = Form(default=None),
    source: str = Form(""),
):
    """multipart 流式上传（B2-04）。

    - 分块写入临时文件，边写边计算 SHA-256 与大小；
    - 超限立即停止并清理临时文件；
    - 写入完成后执行与 Base64 相同的完整安全校验（MIME/扩展名/魔数/像素/页数）；
    - 成功后原子移动到正式附件目录；失败、中断、超限时清理临时文件；
    - 相同内容（sha256）在同一学期内重复上传直接返回已有记录。
    """
    settings = request.app.state.settings
    original_name = file.filename or "upload.bin"
    declared_mime = file.content_type or "application/octet-stream"
    metric_ext = Path(original_name).name.rsplit(".", 1)[-1].lower() if "." in Path(original_name).name else ""
    started = time.monotonic()
    # 指标结果由各返回/异常分支写入，finally 统一上报（不含文件名与正文）
    outcome: dict = {"status": "failed", "code": None, "size": 0}

    # 同盘临时文件保证 os.replace 原子移动
    tmp_path = settings.attachments_dir / f"{TMP_UPLOAD_PREFIX}{secrets.token_hex(8)}{TMP_UPLOAD_SUFFIX}"
    digest = hashlib.sha256()
    size = 0
    try:
        # L1：限制同时写盘的上传数，避免多个大文件打满磁盘与内存
        with upload_slot(DEFAULT_MAX_CONCURRENT_UPLOADS):
            with tmp_path.open("wb") as out:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ATTACHMENT_BYTES:
                        raise upload_http_error(
                            f"单个附件不能超过{MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB",
                            code=UploadErrorCode.TOO_LARGE, status=413,
                        )
                    digest.update(chunk)
                    out.write(chunk)
            outcome["size"] = size

            if size == 0:
                raise upload_http_error(
                    "附件不能为空", code=UploadErrorCode.EMPTY_FILE, status=422,
                )

            digest_hex = digest.hexdigest()
            # 落盘校验（B2-04 增强）：不把整文件读进内存——只读 4096 前缀校验
            # 魔数，哈希逐块计算，图片/PDF 从路径流式解码。校验失败抛 4xx。
            try:
                safe_name = Path(original_name).name
                suffix = Path(safe_name).suffix.lower()
                # L1：与 Base64 路径对齐，拒绝扩展名与声明 MIME 不一致的伪造上传。
                # 声明值可能带参数（如 "text/plain; charset=utf-8"），只比较主类型。
                declared = declared_mime.split(";")[0].strip().lower()
                expected_mime = ALLOWED_EXTENSIONS.get(suffix)
                if expected_mime is None:
                    raise ValueError("不支持的附件格式")
                if declared and declared != "application/octet-stream" and declared != expected_mime:
                    raise ValueError("文件扩展名与 MIME 类型不一致")
                validate_stored_path(
                    settings.attachments_dir,
                    tmp_path.name,
                    expected_size=size,
                    expected_sha256=digest_hex,
                    expected_name=original_name,
                )
                detected_mime = expected_mime
            except ValueError as exc:
                raise upload_http_error(exc) from exc
            with _session(request) as session:
                selected_id = term_id or current_term_id(session)
                require_term(session, selected_id, active_only=True)
                existing = session.scalar(
                    select(Attachment).where(
                        Attachment.term_id == selected_id,
                        Attachment.sha256 == digest_hex,
                    )
                )
                if existing is not None:
                    # 幂等命中：同学期同内容直接复用，不新增存储文件
                    outcome["status"] = "deduped"
                    return existing
                storage_name = f"term_{selected_id}_{secrets.token_hex(16)}{suffix}"
                path = _safe_path(settings, storage_name)
                # 同盘原子移动：不产生中间可读的正式文件
                _os_replace(tmp_path, path)
                metadata: dict = {"security": {"upload_kind": "multipart"}}
                if source.strip():
                    metadata["source"] = source.strip()[:50]
                item = Attachment(
                    term_id=selected_id, title=(title.strip() or safe_name),
                    original_name=safe_name, mime_type=detected_mime,
                    size_bytes=size, sha256=digest_hex, storage_name=storage_name,
                    metadata_json=metadata,
                )
                session.add(item)
                try:
                    session.commit()
                except Exception:
                    session.rollback()
                    # 提交失败：删除已移动的正式文件，避免产生孤儿附件
                    path.unlink(missing_ok=True)
                    outcome["code"] = UploadErrorCode.STORAGE_FAILED
                    raise
                session.refresh(item)
                outcome["status"] = "succeeded"
                return item
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("code"):
            outcome["code"] = str(detail["code"])
        raise
    finally:
        # 任何失败/中断/超限路径都清理临时文件
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        record_upload_metric(
            status=outcome["status"],
            size_bytes=outcome["size"] or size,
            duration_ms=int((time.monotonic() - started) * 1000),
            code=outcome["code"],
            extension=metric_ext,
        )


def _os_replace(src: Path, dst: Path) -> None:
    import os as _os
    _os.replace(src, dst)


@router.get("/{attachment_id}/download")
def download_attachment(attachment_id: int, request: Request):
    with _session(request) as session:
        item = session.get(Attachment, attachment_id)
        if item is None:
            raise HTTPException(404, "附件不存在")
        path = _safe_path(request.app.state.settings, item.storage_name)
        if not path.is_file():
            raise HTTPException(404, "附件文件已丢失")
        return FileResponse(path, media_type=item.mime_type, filename=item.original_name)


@router.delete("/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: int, request: Request):
    with _session(request) as session:
        item = session.get(Attachment, attachment_id)
        if item is None:
            raise HTTPException(404, "附件不存在")
        path = _safe_path(request.app.state.settings, item.storage_name)
        session.execute(
            update(AgentMessageAttachment)
            .where(AgentMessageAttachment.attachment_id == attachment_id)
            .values(attachment_id=None)
        )
        session.execute(
            update(StudentItemResult)
            .where(StudentItemResult.source_attachment_id == attachment_id)
            .values(source_attachment_id=None)
        )
        enqueue_file_operation(session, operation="delete", source=path, size=item.size_bytes, digest=item.sha256)
        session.delete(item)
        session.commit()
        process_pending_file_operations(session)


@router.post("/{attachment_id}/delete-with-workspace", response_model=AttachmentWorkspaceDeleteRead)
def delete_attachment_with_workspace(attachment_id: int, payload: AttachmentWorkspaceDelete, request: Request):
    with _session(request) as session:
        item = session.get(Attachment, attachment_id)
        if item is not None and item.term_id != payload.term_id:
            raise HTTPException(409, "附件不属于当前学期")
        workspace = session.get(WorkspaceState, payload.term_id)
        if workspace is None:
            raise HTTPException(409, "数据已被另一页面修改，请刷新后再操作")
        raw_state = workspace.state_json or {}
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        for documents_key in ("paperDocuments", "archivedDocuments"):
            state[documents_key] = [
                document for document in (state.get(documents_key) or [])
                if not (isinstance(document, dict) and str(document.get("id")) == str(payload.document_id))
            ]
        error_ids = set(payload.error_ids)
        if error_ids:
            state["errors"] = [
                error for error in (state.get("errors") or [])
                if not (isinstance(error, dict) and str(error.get("id")) in error_ids)
            ]
        new_revision = payload.expected_revision + 1
        updated = session.execute(
            update(WorkspaceState)
            .where(WorkspaceState.term_id == payload.term_id, WorkspaceState.revision == payload.expected_revision)
            .values(state_json=state, revision=new_revision)
        )
        if updated.rowcount != 1:
            session.rollback()
            raise HTTPException(409, "数据已被另一页面修改，请刷新后再操作")
        session.add(ChangeLog(entity="attachment", entity_id=str(attachment_id), action="delete_with_workspace", detail_json={"term_id": payload.term_id, "document_id": payload.document_id}))
        if item is not None:
            # 附件可能已经被聊天消息或小题结果引用。删除文件时保留消息/结果，
            # 解除可空外键，避免 SQLite 外键约束把用户确认后的删除变成 500。
            session.execute(
                update(AgentMessageAttachment)
                .where(AgentMessageAttachment.attachment_id == attachment_id)
                .values(attachment_id=None)
            )
            session.execute(
                update(StudentItemResult)
                .where(StudentItemResult.source_attachment_id == attachment_id)
                .values(source_attachment_id=None)
            )
            path = _safe_path(request.app.state.settings, item.storage_name)
            enqueue_file_operation(session, operation="delete", source=path, size=item.size_bytes, digest=item.sha256)
            session.delete(item)
        session.commit()
        process_pending_file_operations(session)
        return AttachmentWorkspaceDeleteRead(attachment_id=attachment_id, revision=new_revision, state=state)
