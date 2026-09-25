from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from ..auth import require_token
from ..models import BackupRecord, ChangeLog, WorkspaceState
from ..schemas import WorkspaceStateRead, WorkspaceStateWrite
from ..services.backups import create_backup, sha256
from ..services.growth.exam_events import sync_exam_growth_events
from ..services.terms import current_term_id, require_term
from ..services.workspace_sync import sync_workspace_domains

router = APIRouter(prefix="/api/v1/workspace-state", dependencies=[Depends(require_token)])
scoped_router = APIRouter(prefix="/api/v1/terms/{term_id}/workspace-state", dependencies=[Depends(require_token)])


def _session(request: Request):
    return request.app.state.session_factory()


def _read_workspace_state(term_id: int, request: Request):
    with _session(request) as session:
        require_term(session, term_id)
        item = session.get(WorkspaceState, term_id)
        if item is None:
            return WorkspaceStateRead(state=None, revision=0)
        return WorkspaceStateRead(
            state=item.state_json,
            revision=item.revision,
            updated_at=item.updated_at,
        )


@router.get("", response_model=WorkspaceStateRead)
def read_workspace_state(request: Request):
    with _session(request) as session:
        term_id = current_term_id(session)
    return _read_workspace_state(term_id, request)


@scoped_router.get("", response_model=WorkspaceStateRead)
def read_scoped_workspace_state(term_id: int, request: Request):
    return _read_workspace_state(term_id, request)


def _write_workspace_state(term_id: int, payload: WorkspaceStateWrite, request: Request):
    now = datetime.now(timezone.utc)
    with _session(request) as session:
        require_term(session, term_id, active_only=True)
        if payload.expected_revision == 0:
            if session.get(WorkspaceState, term_id) is not None:
                raise HTTPException(409, "数据已被另一页面修改，请刷新后再操作")
            item = WorkspaceState(
                term_id=term_id, state_json=payload.state, revision=1, updated_at=now
            )
            session.add(item)
            next_revision = 1
        else:
            result = session.execute(
                update(WorkspaceState)
                .where(
                    WorkspaceState.term_id == term_id,
                    WorkspaceState.revision == payload.expected_revision,
                )
                .values(
                    state_json=payload.state,
                    revision=payload.expected_revision + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "数据已被另一页面修改，请刷新后再操作")
            next_revision = payload.expected_revision + 1
        session.add(ChangeLog(
            entity="workspace_state",
            entity_id=str(term_id),
            action="create" if payload.expected_revision == 0 else "update",
            detail_json={"revision": next_revision},
        ))
        try:
            sync_workspace_domains(session, term_id, payload.state)
            # 考试生效后自动生成「参加可比测评」事件与进步分（幂等，重复推送不重复计分）。
            sync_exam_growth_events(session, term_id=term_id)
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "数据已被另一页面修改，请刷新后再操作") from error
        item = session.get(WorkspaceState, term_id)
        return WorkspaceStateRead(
            state=item.state_json,
            revision=item.revision,
            updated_at=item.updated_at,
        )


@router.put("", response_model=WorkspaceStateRead)
def write_workspace_state(payload: WorkspaceStateWrite, request: Request):
    with _session(request) as session:
        term_id = current_term_id(session)
    return _write_workspace_state(term_id, payload, request)


@scoped_router.put("", response_model=WorkspaceStateRead)
def write_scoped_workspace_state(term_id: int, payload: WorkspaceStateWrite, request: Request):
    return _write_workspace_state(term_id, payload, request)


def _backup_workspace_state(term_id: int, request: Request):
    settings = request.app.state.settings
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination = settings.backups_dir / f"term_{term_id}_before_replace_{timestamp}"
    create_backup(
        settings.data_dir / "workbench.db",
        destination,
        kind="before_replace",
        metadata={"term_id": term_id},
        attachments_dir=settings.attachments_dir,
    )
    checksum = sha256(destination / "workbench.db")
    with _session(request) as session:
        session.add(BackupRecord(
            path=str(destination),
            checksum=checksum,
            kind="before_replace",
        ))
        session.commit()
    return {"name": destination.name, "checksum": checksum}


@router.post("/backup")
def backup_workspace_state(request: Request):
    with _session(request) as session:
        term_id = current_term_id(session)
    return _backup_workspace_state(term_id, request)


@scoped_router.post("/backup")
def backup_scoped_workspace_state(term_id: int, request: Request):
    with _session(request) as session:
        require_term(session, term_id)
    return _backup_workspace_state(term_id, request)
