from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..version import SCHEMA_REVISION


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attachment_entries(database: Path, destination: Path, attachments_dir: Path | None) -> list[dict]:
    if attachments_dir is None:
        return []
    target_root = destination / "attachments"
    entries: list[dict] = []
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        try:
            rows = connection.execute(
                "SELECT id, term_id, original_name, mime_type, size_bytes, sha256, storage_name "
                "FROM attachments"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    for attachment_id, term_id, original_name, mime_type, size_bytes, digest, storage_name in rows:
        source = (attachments_dir / storage_name).resolve()
        root = attachments_dir.resolve()
        if root not in source.parents or not source.is_file():
            raise ValueError(f"附件文件不存在或路径无效：{storage_name}")
        target_root.mkdir(parents=True, exist_ok=True)
        target = target_root / storage_name
        shutil.copy2(source, target)
        actual_size = target.stat().st_size
        actual_digest = sha256(target)
        if actual_size != size_bytes or actual_digest != digest:
            raise ValueError(f"附件校验和不匹配：{storage_name}")
        entries.append({
            "path": f"attachments/{storage_name}",
            "attachment_id": attachment_id,
            "term_id": term_id,
            "original_name": original_name,
            "mime_type": mime_type,
            "size": actual_size,
            "sha256": actual_digest,
        })
    return entries


def verify_backup(backup_dir: Path) -> dict:
    db = backup_dir / "workbench.db"
    manifest = backup_dir / "manifest.json"
    checksum = backup_dir / "checksum.sha256"
    if not all(item.is_file() for item in (db, manifest, checksum)):
        raise ValueError("备份目录缺少 workbench.db、manifest.json 或 checksum.sha256")
    expected = checksum.read_text(encoding="utf-8").strip().split()[0]
    actual = sha256(db)
    if expected != actual:
        raise ValueError("数据库校验和不匹配，备份可能已损坏")
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ValueError(f"数据库完整性检查失败：{integrity}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    attachments = data.get("attachments", [])
    for item in attachments:
        relative = Path(item.get("path", ""))
        target = (backup_dir / relative).resolve()
        if relative.is_absolute() or backup_dir.resolve() not in target.parents or not target.is_file():
            raise ValueError(f"备份附件路径无效或文件缺失：{relative}")
        if target.stat().st_size != item.get("size") or sha256(target) != item.get("sha256"):
            raise ValueError(f"备份附件校验和不匹配：{relative}")
    return {"valid": True, "manifest": data, "sha256": actual, "integrity": integrity}


def restore_backup(backup_dir: Path, data_dir: Path, *, expected_schema: str | None = SCHEMA_REVISION) -> Path:
    """Restore a verified backup into an offline data directory.

    The caller must stop the application first. Validation happens before any
    live path is changed; the previous data is retained as a safety copy.
    """
    backup_dir = backup_dir.resolve()
    data_dir = data_dir.resolve()
    result = verify_backup(backup_dir)
    manifest = result["manifest"]
    # 旧版 create_all() 数据库没有 alembic_version，只能标记为
    # ``unversioned``；这种备份仍可恢复并交给启动迁移补齐版本。
    if expected_schema and manifest.get("schema") not in {expected_schema, "unversioned"}:
        raise ValueError(f"备份数据库版本不匹配：{manifest.get('schema')}")

    data_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safety_dir = data_dir.parent / f"{data_dir.name}_before_restore_{timestamp}"
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{data_dir.name}_restore_", dir=data_dir.parent))
    try:
        staged_db = staging_dir / "workbench.db"
        shutil.copy2(backup_dir / "workbench.db", staged_db)
        shutil.copy2(backup_dir / "manifest.json", staging_dir / "manifest.json")
        shutil.copy2(backup_dir / "checksum.sha256", staging_dir / "checksum.sha256")
        staged_attachments = staging_dir / "attachments"
        if (backup_dir / "attachments").is_dir():
            shutil.copytree(backup_dir / "attachments", staged_attachments)
        verify_backup(staging_dir)

        if data_dir.exists():
            data_dir.replace(safety_dir)
        staging_dir.replace(data_dir)
        return safety_dir
    except Exception:
        if data_dir.exists() and not (data_dir / "workbench.db").exists():
            shutil.rmtree(data_dir, ignore_errors=True)
        if safety_dir.exists() and not data_dir.exists():
            safety_dir.replace(data_dir)
        raise
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def _export_entries(database: Path, destination: Path, exports_dir: Path | None) -> list[dict]:
    """L4：把已登记的导出产物纳入备份（方案 §11.5 产物备份与审计）。"""
    if exports_dir is None:
        return []
    target_root = destination / "exports"
    entries: list[dict] = []
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        try:
            rows = connection.execute(
                "SELECT id, storage_name, mime_type, size_bytes, sha256, contains_personal_data "
                "FROM generated_artifacts"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    for artifact_id, storage_name, mime_type, size_bytes, digest, personal in rows:
        source = (exports_dir / storage_name).resolve()
        root = exports_dir.resolve()
        if root not in source.parents or not source.is_file():
            # 产物可能已被过期清理，跳过而非失败（备份不阻塞主流程）
            continue
        target_root.mkdir(parents=True, exist_ok=True)
        target = target_root / storage_name
        shutil.copy2(source, target)
        actual_size = target.stat().st_size
        actual_digest = sha256(target)
        entries.append({
            "path": f"exports/{storage_name}",
            "artifact_id": artifact_id,
            "mime_type": mime_type,
            "size": actual_size,
            "sha256": actual_digest,
            "contains_personal_data": bool(personal),
        })
    return entries


def create_backup(
    database: Path,
    destination: Path,
    *,
    kind: str = "manual",
    metadata: dict | None = None,
    attachments_dir: Path | None = None,
    exports_dir: Path | None = None,
) -> Path:
    if not database.is_file():
        raise FileNotFoundError(database)
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / "workbench.db"
    source_uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source, sqlite3.connect(target) as backup:
        source.backup(backup)
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"备份数据库完整性检查失败：{integrity}")
    attachment_entries = _attachment_entries(database, destination, attachments_dir)
    export_entries = _export_entries(database, destination, exports_dir)
    digest = sha256(target)
    (destination / "checksum.sha256").write_text(f"{digest}  workbench.db\n", encoding="utf-8")
    schema_revision = _read_schema_revision(target)
    manifest = {
        "kind": kind,
        "schema": schema_revision,
        "sha256": digest,
        "database": {"path": "workbench.db", "size": target.stat().st_size, "sha256": digest},
        "attachments": attachment_entries,
        "exports": export_entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_name": database.name,
    }
    if metadata:
        manifest["metadata"] = metadata
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destination


def _read_schema_revision(database: Path) -> str:
    """读取备份数据库自身的 Alembic revision，避免标记成当前代码版本。"""
    try:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
        return str(row[0]) if row and row[0] else "unversioned"
    except sqlite3.Error:
        return "unversioned"
