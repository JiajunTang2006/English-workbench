from __future__ import annotations

from pathlib import Path
from sqlalchemy import select

from ..models import PendingFileOperation
from .backups import sha256

MAX_FILE_OPERATION_ATTEMPTS = 10


def enqueue_file_operation(session, *, operation: str, source: Path, target: Path | None = None, size: int | None = None, digest: str | None = None) -> PendingFileOperation:
    item = PendingFileOperation(
        operation=operation,
        source_path=str(source),
        target_path=str(target) if target else None,
        expected_size=size,
        expected_sha256=digest,
        status="pending",
    )
    session.add(item)
    session.flush()
    return item


def _matches(path: Path, size: int | None, digest: str | None) -> bool:
    return path.is_file() and (size is None or path.stat().st_size == size) and (digest is None or sha256(path) == digest)


def process_pending_file_operations(session) -> int:
    completed = 0
    for item in session.scalars(select(PendingFileOperation).where(PendingFileOperation.status.in_(("pending", "failed"))).order_by(PendingFileOperation.id)).all():
        if item.attempts >= MAX_FILE_OPERATION_ATTEMPTS:
            # 保留失败记录供诊断，但不让每次启动都无限重试同一个坏路径。
            continue
        item.attempts += 1
        try:
            source = Path(item.source_path)
            target = Path(item.target_path) if item.target_path else None
            if item.operation == "move":
                if target is None:
                    raise ValueError("移动操作缺少目标路径")
                if target.is_file() and _matches(target, item.expected_size, item.expected_sha256):
                    source.unlink(missing_ok=True)
                elif source.is_file():
                    if not _matches(source, item.expected_size, item.expected_sha256):
                        raise ValueError("源文件校验和或大小不匹配")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.replace(target)
                elif target.exists():
                    raise ValueError("目标文件已存在但校验和或大小不匹配")
                else:
                    raise FileNotFoundError(source)
            elif item.operation == "delete":
                if source.exists() and not _matches(source, item.expected_size, item.expected_sha256):
                    raise ValueError("待删除文件校验和或大小不匹配")
                source.unlink(missing_ok=True)
            else:
                raise ValueError(f"未知文件操作：{item.operation}")
            item.status = "completed"
            item.last_error = None
            completed += 1
        except (OSError, ValueError) as error:
            item.status = "failed"
            item.last_error = str(error)
    session.commit()
    return completed
