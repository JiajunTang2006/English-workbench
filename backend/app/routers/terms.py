from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select, update

from ..auth import require_token
from ..models import AppSetting, Attachment, BackupRecord, ChangeLog, Class, Enrollment, Exam, ExamScore, Student, Term, WorkspaceState
from ..schemas import CurrentTermWrite, TermCacheClear, TermCacheSummary, TermCreate, TermDelete, TermPatch, TermRead
from ..services.backups import create_backup, sha256, verify_backup
from ..services.file_operations import enqueue_file_operation, process_pending_file_operations
from ..services.student_profiles import prepare_student_profile_inheritance
from ..services.term_cache import cache_summary, clear_cache
from ..services.terms import clone_workspace_state, current_term_id, require_term


router = APIRouter(prefix="/api/v1/terms", tags=["terms"], dependencies=[Depends(require_token)])


def _session(request: Request):
    return request.app.state.session_factory()


@router.get("", response_model=list[TermRead])
def list_terms(request: Request, include_archived: bool = False):
    with _session(request) as session:
        query = select(Term).order_by(Term.starts_on.desc(), Term.id.desc())
        if not include_archived:
            query = query.where(Term.status == "active")
        return list(session.scalars(query))


@router.get("/current", response_model=TermRead)
def read_current_term(request: Request):
    with _session(request) as session:
        return require_term(session, current_term_id(session))


@router.put("/current", response_model=TermRead)
def set_current_term(payload: CurrentTermWrite, request: Request):
    with _session(request) as session:
        term = require_term(session, payload.term_id, active_only=True)
        previous_term_id = current_term_id(session)
        if previous_term_id != term.id:
            prepare_student_profile_inheritance(session, previous_term_id, term.id)
        setting = session.get(AppSetting, "active_term_id")
        if setting:
            setting.value_json = term.id
        else:
            session.add(AppSetting(key="active_term_id", value_json=term.id))
        session.commit()
        return term


@router.get("/{term_id}/cache", response_model=TermCacheSummary)
def read_term_cache(term_id: int, request: Request):
    """查看指定学期可清理的 TeachMate AI 缓存数量。"""
    with _session(request) as session:
        try:
            return cache_summary(session, term_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{term_id}/cache/clear")
def clear_term_cache(term_id: int, payload: TermCacheClear, request: Request):
    """清理指定学期的 TeachMate AI 缓存，不影响正式教学数据。"""
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="请确认清理该学期的 AI 缓存")
    with _session(request) as session:
        try:
            result = clear_cache(
                session,
                term_id,
                data_dir=getattr(request.app.state.settings, "data_dir", None),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, **result}


@router.post("", response_model=TermRead, status_code=201)
def create_term(payload: TermCreate, request: Request):
    with _session(request) as session:
        if session.scalar(select(Term.id).where(Term.code == payload.code)):
            raise HTTPException(409, "学期代码已存在")
        source = require_term(session, payload.clone_from_term_id) if payload.clone_from_term_id else None
        term = Term(
            code=payload.code,
            name=payload.name.strip(),
            starts_on=payload.starts_on,
            ends_on=payload.ends_on,
        )
        session.add(term)
        session.flush()
        class_map: dict[int, int] = {}
        if source and payload.clone_classes:
            for classroom in session.scalars(select(Class).where(Class.term_id == source.id, Class.status == "active")):
                copied = Class(
                    term_id=term.id,
                    name=classroom.name,
                    grade=classroom.grade,
                    school_year=classroom.school_year,
                )
                session.add(copied)
                session.flush()
                class_map[classroom.id] = copied.id
        if source and payload.clone_enrollments:
            for enrollment in session.scalars(select(Enrollment).where(
                Enrollment.term_id == source.id,
                Enrollment.status == "active",
            )):
                target_class_id = class_map.get(enrollment.class_id)
                if target_class_id:
                    session.add(Enrollment(
                        term_id=term.id,
                        class_id=target_class_id,
                        student_id=enrollment.student_id,
                        entrance_english=enrollment.entrance_english,
                        target_score=enrollment.target_score,
                        weak_tags=enrollment.weak_tags,
                        parent_phone=enrollment.parent_phone,
                        seat=enrollment.seat,
                    ))
        if source:
            source_workspace = session.get(WorkspaceState, source.id)
            if source_workspace:
                session.add(WorkspaceState(
                    term_id=term.id,
                    state_json=clone_workspace_state(source_workspace, include_roster=payload.clone_enrollments),
                    revision=1,
                ))
        session.commit()
        session.refresh(term)
        return term


@router.patch("/{term_id}", response_model=TermRead)
def update_term(term_id: int, payload: TermPatch, request: Request):
    with _session(request) as session:
        term = require_term(session, term_id, active_only=True)
        values = payload.model_dump(exclude_unset=True)
        starts_on = values.get("starts_on", term.starts_on)
        ends_on = values.get("ends_on", term.ends_on)
        if starts_on and ends_on and starts_on > ends_on:
            raise HTTPException(422, "学期开始日期不能晚于结束日期")
        if "code" in values and session.scalar(select(Term.id).where(Term.code == values["code"], Term.id != term.id)):
            raise HTTPException(409, "学期代码已存在")
        for key, value in values.items():
            setattr(term, key, value.strip() if isinstance(value, str) else value)
        session.commit()
        session.refresh(term)
        return term


@router.post("/{term_id}/archive", response_model=TermRead)
def archive_term(term_id: int, request: Request):
    with _session(request) as session:
        term = require_term(session, term_id, active_only=True)
        if current_term_id(session) == term.id:
            raise HTTPException(409, "请先切换到其他学期")
        active_count = len(list(session.scalars(select(Term.id).where(Term.status == "active"))))
        if active_count <= 1:
            raise HTTPException(409, "至少保留一个可用学期")
        term.status = "archived"
        session.commit()
        session.refresh(term)
        return term


@router.delete("/{term_id}", status_code=204)
def permanently_delete_term(term_id: int, payload: TermDelete, request: Request):
    """永久删除学期及其学期范围数据；当前学期和最后一个可用学期不能删除。"""
    # 先完成所有只读校验，避免无效请求产生无用备份。
    with _session(request) as inspect_session:
        term = inspect_session.get(Term, term_id)
        if term is None:
            raise HTTPException(404, "学期不存在")
        if current_term_id(inspect_session) == term.id:
            raise HTTPException(409, "请先切换到其他学期")
        if payload.confirmation_code.strip() != term.code:
            raise HTTPException(422, "请输入正确的学期代码确认删除")
        if term.status == "active":
            active_count = inspect_session.scalar(select(func.count(Term.id)).where(Term.status == "active")) or 0
            if active_count <= 1:
                raise HTTPException(409, "至少保留一个可用学期")
        attachment_rows = list(inspect_session.scalars(select(Attachment).where(Attachment.term_id == term_id)))
        class_ids = list(inspect_session.scalars(select(Class.id).where(Class.term_id == term_id)))
        attachment_snapshot = [
            (item.id, item.storage_name, item.size_bytes, item.sha256) for item in attachment_rows
        ]
        impact = {
            "term_id": term_id,
            "classes": len(class_ids),
            "enrollments": inspect_session.scalar(select(func.count(Enrollment.id)).where(Enrollment.term_id == term_id)) or 0,
            "exams": inspect_session.scalar(select(func.count(Exam.id)).where(Exam.term_id == term_id)) or 0,
            "exam_scores": inspect_session.scalar(select(func.count(ExamScore.id)).join(Exam, Exam.id == ExamScore.exam_id).where(Exam.term_id == term_id)) or 0,
            "workspace_state": 1 if inspect_session.get(WorkspaceState, term_id) else 0,
            "attachments": len(attachment_rows),
            "attachment_bytes": sum(item.size_bytes for item in attachment_rows),
            "attachment_names": [item.storage_name for item in attachment_rows],
        }
    settings = request.app.state.settings
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination = settings.backups_dir / f"term_{term_id}_before_delete_{timestamp}"
    try:
        create_backup(
            settings.data_dir / "workbench.db", destination,
            kind="before_term_delete", metadata=impact,
            attachments_dir=settings.attachments_dir,
        )
        verification = verify_backup(destination)
    except (OSError, ValueError) as error:
        raise HTTPException(503, f"删除前自动备份失败，已停止删除：{error}") from error
    with _session(request) as backup_session:
        backup_session.add(BackupRecord(path=str(destination), checksum=verification["sha256"], kind="before_term_delete"))
        backup_session.commit()

    with _session(request) as session:
        term = session.get(Term, term_id)
        if term is None:
            raise HTTPException(404, "学期不存在")
        if current_term_id(session) == term.id or payload.confirmation_code.strip() != term.code:
            raise HTTPException(409, "学期状态已变化，请刷新后重试")
        if term.status == "active" and (session.scalar(select(func.count(Term.id)).where(Term.status == "active")) or 0) <= 1:
            raise HTTPException(409, "至少保留一个可用学期")
        current_attachments = list(session.scalars(select(Attachment).where(Attachment.term_id == term_id)))
        current_snapshot = [
            (item.id, item.storage_name, item.size_bytes, item.sha256) for item in current_attachments
        ]
        if current_snapshot != attachment_snapshot:
            raise HTTPException(409, "学期附件已变化，请刷新后重试")
        attachment_rows = current_attachments

        if class_ids:
            # 学生是跨学期共享的实体，只清空旧班级镜像，不删除学生本身。
            session.execute(update(Student).where(Student.class_id.in_(class_ids)).values(class_id=None))
            session.execute(delete(Enrollment).where(Enrollment.term_id == term.id))

        # Exam 的子表通过 ORM cascade 删除；先删考试再删班级，避免外键阻塞。
        for exam in session.scalars(select(Exam).where(Exam.term_id == term.id)).all():
            session.delete(exam)
        session.flush()
        workspace = session.get(WorkspaceState, term.id)
        if workspace:
            session.delete(workspace)
        session.execute(delete(Class).where(Class.term_id == term.id))
        for attachment in attachment_rows:
            source = (settings.attachments_dir / attachment.storage_name).resolve()
            target = (destination / "recycle" / attachment.storage_name).resolve()
            enqueue_file_operation(
                session,
                operation="move",
                source=source,
                target=target,
                size=attachment.size_bytes,
                digest=attachment.sha256,
            )
        session.add(ChangeLog(
            entity="term",
            entity_id=str(term.id),
            action="permanent_delete",
            detail_json={"impact": impact, "backup": str(destination), "backup_sha256": verification["sha256"]},
        ))
        session.delete(term)
        session.commit()
        process_pending_file_operations(session)


@router.post("/{term_id}/restore", response_model=TermRead)
def restore_term(term_id: int, request: Request):
    with _session(request) as session:
        term = session.get(Term, term_id)
        if term is None:
            raise HTTPException(404, "学期不存在")
        term.status = "active"
        session.commit()
        session.refresh(term)
        return term
