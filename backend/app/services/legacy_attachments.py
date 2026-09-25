from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from pathlib import Path

from sqlalchemy import select

from ..models import Attachment, WorkspaceState
from .backups import create_backup

MAX_LEGACY_BYTES = 50 * 1024 * 1024


def migrate_legacy_documents(session, settings, *, create_preflight_backup: bool = True) -> int:
    """将旧快照内的 Base64 原卷迁移到附件目录；失败时保留原快照以便重试。"""
    if create_preflight_backup:
        timestamp = secrets.token_hex(8)
        create_backup(
            settings.data_dir / "workbench.db",
            settings.backups_dir / f"before_attachment_migration_{timestamp}",
            kind="before_attachment_migration",
            attachments_dir=settings.attachments_dir,
        )
    migrated = 0
    for workspace in session.scalars(select(WorkspaceState)).all():
        state = json.loads(json.dumps(workspace.state_json or {}))
        documents = state.get("paperDocuments") or []
        changed = False
        created_paths: list[Path] = []
        try:
            for document in documents:
                content = document.get("content")
                if not content or document.get("attachmentId"):
                    continue
                try:
                    raw = base64.b64decode(content, validate=True)
                except (ValueError, binascii.Error) as error:
                    raise ValueError(f"原卷 {document.get('id', '')} 不是有效的 base64") from error
                if not raw or len(raw) > MAX_LEGACY_BYTES:
                    raise ValueError("旧版原卷为空或超过50MB")
                digest = hashlib.sha256(raw).hexdigest()
                existing = session.scalar(select(Attachment).where(Attachment.term_id == workspace.term_id, Attachment.sha256 == digest))
                if existing:
                    item = existing
                else:
                    suffix = Path(document.get("name", "paper.bin")).suffix.lower()[:16]
                    storage_name = f"term_{workspace.term_id}_legacy_{digest[:24]}{suffix}"
                    path = settings.attachments_dir / storage_name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.is_file():
                        path.write_bytes(raw)
                        created_paths.append(path)
                    item = Attachment(term_id=workspace.term_id, title=document.get("name", "原卷"), original_name=Path(document.get("name", "paper.bin")).name, mime_type=document.get("mimeType", "application/octet-stream"), size_bytes=len(raw), sha256=digest, storage_name=storage_name, metadata_json={"legacy_document_id": document.get("id")})
                    session.add(item)
                    session.flush()
                metadata = {key: value for key, value in document.items() if key != "content"}
                metadata["attachmentId"] = item.id
                document.clear()
                document.update(metadata)
                changed = True
                migrated += 1
            if changed:
                workspace.state_json = state
                workspace.revision += 1
        except Exception:
            for path in created_paths:
                path.unlink(missing_ok=True)
            session.rollback()
            raise
    session.commit()
    return migrated
