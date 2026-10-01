from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_token
from ..models import AppSetting, ChangeLog, Class, Enrollment, Student, WorkspaceState
from ..schemas import (
    ClassCreate,
    ClassPatch,
    ClassRead,
    SettingsPatch,
    SettingsRead,
    StudentCreate,
    StudentPatch,
    StudentRead,
    SubjectsRead,
)
from ..services.subjects import DEFAULT_SUBJECT_KEY, get_subject, list_subjects, normalize_subject_key, subject_switch_blocker
from ..services.terms import current_term_id, require_term
from ..version import SCHEMA_REVISION

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


def get_session(request: Request) -> Session:
    return request.app.state.session_factory()


def _student_read(item: Student, enrollment: Enrollment) -> StudentRead:
    """Return the student's fields in the requested term, not the legacy mirror."""
    return StudentRead(
        id=item.id,
        student_no=item.student_no,
        name=item.name,
        class_id=enrollment.class_id,
        gender=item.gender,
        entrance_english=enrollment.entrance_english,
        target_score=enrollment.target_score,
        weak_tags=enrollment.weak_tags,
        parent_phone=enrollment.parent_phone,
        seat=enrollment.seat,
        status=enrollment.status,
    )


@router.get("/runtime")
def runtime(request: Request):
    return {
        "service": "english-workbench",
        "host": request.app.state.settings.host,
        "schema": SCHEMA_REVISION,
        "version": request.app.version,
    }


@router.get("/classes", response_model=list[ClassRead])
def list_classes(request: Request, include_archived: bool = False, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        require_term(session, selected_term_id)
        query = select(Class).where(Class.term_id == selected_term_id).order_by(Class.name)
        if not include_archived:
            query = query.where(Class.status == "active")
        return list(session.scalars(query))


@router.post("/classes", response_model=ClassRead, status_code=201)
def create_class(payload: ClassCreate, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        require_term(session, selected_term_id, active_only=True)
        if session.scalar(select(Class).where(Class.term_id == selected_term_id, Class.name == payload.name)):
            raise HTTPException(409, "班级名称已存在")
        item = Class(term_id=selected_term_id, **payload.model_dump())
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


@router.patch("/classes/{class_id}", response_model=ClassRead)
def update_class(class_id: int, payload: ClassPatch, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        item = session.get(Class, class_id)
        if not item or item.term_id != selected_term_id or item.status != "active":
            raise HTTPException(404, "班级不存在")
        values = payload.model_dump(exclude_unset=True)
        if "name" in values and session.scalar(select(Class).where(Class.term_id == selected_term_id, Class.name == values["name"], Class.id != class_id)):
            raise HTTPException(409, "班级名称已存在")
        for key, value in values.items():
            setattr(item, key, value.strip() if isinstance(value, str) else value)
        session.commit()
        session.refresh(item)
        return item


@router.post("/classes/{class_id}/archive", response_model=ClassRead)
def archive_class(class_id: int, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        item = session.get(Class, class_id)
        if not item or item.term_id != selected_term_id:
            raise HTTPException(404, "班级不存在")
        active_students = session.scalar(
            select(Enrollment.id).where(
                Enrollment.term_id == selected_term_id,
                Enrollment.class_id == class_id,
                Enrollment.status == "active",
            ).limit(1)
        )
        if active_students is not None:
            raise HTTPException(409, "班级仍有在读学生，请先转班或归档学生")
        item.status = "archived"
        session.commit()
        session.refresh(item)
        return item


@router.get("/students", response_model=list[StudentRead])
def list_students(request: Request, class_id: int | None = None, search: str = Query(default="", max_length=100), include_archived: bool = False, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        require_term(session, selected_term_id)
        query = select(Student, Enrollment).join(Enrollment).where(Enrollment.term_id == selected_term_id).order_by(Student.student_no)
        if class_id:
            query = query.where(Enrollment.class_id == class_id)
        if search.strip():
            value = search.strip()
            query = query.where(Student.name.contains(value) | Student.student_no.contains(value))
        if not include_archived:
            query = query.where(Enrollment.status == "active")
        return [_student_read(student, enrollment) for student, enrollment in session.execute(query)]


def _apply_student_workspace_change(session: Session, term_id: int, student: Student, action: str, expected_revision: int | None) -> None:
    workspace = session.get(WorkspaceState, term_id)
    if workspace is None:
        if expected_revision is None:
            return
        raise HTTPException(409, "工作台快照不存在，请先初始化工作台")
    if expected_revision is not None and workspace.revision != expected_revision:
        raise HTTPException(409, "工作台版本已变化，请刷新后重试")
    state = dict(workspace.state_json or {})
    students = list(state.get("students") or [])
    for record in students:
        if str(record.get("id")) == str(student.id) or str(record.get("studentNo", record.get("student_no", ""))) == student.student_no:
            record["status"] = "active" if action == "restore" else action
    state["students"] = students
    workspace.state_json = state
    workspace.revision += 1
    session.add(ChangeLog(entity="student", entity_id=str(student.id), action=action, detail_json={"term_id": term_id, "revision": workspace.revision}))


def _validate_class(session: Session, class_id: int, term_id: int) -> None:
    item = session.get(Class, class_id)
    if not item or item.term_id != term_id or item.status != "active":
        raise HTTPException(400, "目标班级不存在或已归档")


@router.post("/students", response_model=StudentRead, status_code=201)
def create_student(payload: StudentCreate, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        _validate_class(session, payload.class_id, selected_term_id)
        item = session.scalar(select(Student).where(Student.student_no == payload.student_no))
        if item is not None:
            existing = session.scalar(select(Enrollment).where(
                Enrollment.term_id == selected_term_id,
                Enrollment.student_id == item.id,
            ))
            if existing is not None:
                raise HTTPException(409, "该学生已在本学期名单中")
            item.name = payload.name
            item.gender = payload.gender
        else:
            item = Student(**payload.model_dump())
            session.add(item)
            session.flush()
        enrollment = Enrollment(
            term_id=selected_term_id,
            class_id=payload.class_id,
            student_id=item.id,
            entrance_english=payload.entrance_english,
            target_score=payload.target_score,
            weak_tags=payload.weak_tags,
            parent_phone=payload.parent_phone,
            seat=payload.seat,
        )
        session.add(enrollment)
        if selected_term_id == current_term_id(session):
            item.class_id = payload.class_id
        item.status = "active"
        session.commit()
        session.refresh(item)
        return _student_read(item, enrollment)


@router.patch("/students/{student_id}", response_model=StudentRead)
def update_student(student_id: int, payload: StudentPatch, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        item = session.get(Student, student_id)
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.term_id == selected_term_id,
            Enrollment.student_id == student_id,
            Enrollment.status == "active",
        ))
        if not item or not enrollment:
            raise HTTPException(404, "学生不存在")
        values = payload.model_dump(exclude_unset=True)
        if "class_id" in values:
            _validate_class(session, values["class_id"], selected_term_id)
            next_class_id = values.pop("class_id")
            enrollment.class_id = next_class_id
            if selected_term_id == current_term_id(session):
                item.class_id = next_class_id
        if "student_no" in values and session.scalar(select(Student).where(Student.student_no == values["student_no"], Student.id != student_id)):
            raise HTTPException(409, "学号已存在")
        term_profile_keys = {"entrance_english", "target_score", "weak_tags", "parent_phone", "seat"}
        for key in term_profile_keys & values.keys():
            setattr(enrollment, key, values.pop(key))
        for key, value in values.items():
            setattr(item, key, value.strip() if isinstance(value, str) else value)
        if selected_term_id == current_term_id(session):
            for key in term_profile_keys:
                setattr(item, key, getattr(enrollment, key))
        session.commit()
        session.refresh(item)
        return _student_read(item, enrollment)


@router.post("/students/{student_id}/archive", response_model=StudentRead)
def archive_student(student_id: int, request: Request, term_id: int | None = Query(default=None, gt=0), expected_revision: int | None = Query(default=None, ge=0)):
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        item = session.get(Student, student_id)
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.term_id == selected_term_id,
            Enrollment.student_id == student_id,
            Enrollment.status == "active",
        ))
        if not item or not enrollment:
            raise HTTPException(404, "学生不存在")
        enrollment.status = "archived"
        enrollment.left_on = datetime.now(timezone.utc).date()
        if session.scalar(select(Enrollment.id).where(
            Enrollment.student_id == student_id,
            Enrollment.status == "active",
            Enrollment.id != enrollment.id,
        ).limit(1)) is None:
            item.status = "archived"
            item.archived_at = datetime.now(timezone.utc)
        _apply_student_workspace_change(session, selected_term_id, item, "archive", expected_revision)
        session.commit()
        session.refresh(item)
        return _student_read(item, enrollment)


@router.post("/students/{student_id}/restore", response_model=StudentRead)
def restore_student(student_id: int, request: Request, term_id: int | None = Query(default=None, gt=0), expected_revision: int | None = Query(default=None, ge=0)):
    """恢复指定学期的学生归属，不依赖完整快照回写。"""
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        require_term(session, selected_term_id, active_only=True)
        item = session.get(Student, student_id)
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.term_id == selected_term_id,
            Enrollment.student_id == student_id,
        ))
        if not item or not enrollment:
            raise HTTPException(404, "学生不存在")
        enrollment.status = "active"
        enrollment.left_on = None
        item.status = "active"
        item.archived_at = None
        _apply_student_workspace_change(session, selected_term_id, item, "restore", expected_revision)
        session.commit()
        session.refresh(item)
        return _student_read(item, enrollment)


@router.post("/students/{student_id}/purge", response_model=StudentRead)
def purge_student(student_id: int, request: Request, term_id: int | None = Query(default=None, gt=0), expected_revision: int | None = Query(default=None, ge=0)):
    """将本学期归属标记为 purged，保留成绩历史，避免快照误删造成不可逆数据损失。"""
    with get_session(request) as session:
        selected_term_id = term_id or current_term_id(session)
        require_term(session, selected_term_id, active_only=True)
        item = session.get(Student, student_id)
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.term_id == selected_term_id,
            Enrollment.student_id == student_id,
        ))
        if not item or not enrollment:
            raise HTTPException(404, "学生不存在")
        enrollment.status = "purged"
        enrollment.left_on = datetime.now(timezone.utc).date()
        item.status = "archived"
        item.archived_at = datetime.now(timezone.utc)
        _apply_student_workspace_change(session, selected_term_id, item, "purge", expected_revision)
        session.commit()
        session.refresh(item)
        return _student_read(item, enrollment)


DEFAULT_SETTINGS = SettingsRead().model_dump()


def _stored_settings(session: Session) -> dict:
    """安装级设置的「默认值 + 库中已存值」，并对学科 key 做读路径容错归一。"""
    values = DEFAULT_SETTINGS.copy()
    for item in session.scalars(select(AppSetting)):
        if item.key in values:
            values[item.key] = item.value_json
    values["subject_key"] = normalize_subject_key(values.get("subject_key"))
    return values


@router.get("/subjects", response_model=SubjectsRead)
def read_subjects(request: Request):
    """学科清单 + 当前学科 + 老师是否已显式选过学科。

    首次进入时前端据此弹出「选择学科」；已选过则直接按当前学科渲染界面。
    学科是安装级设置，换学期不需要重选，要换在设置页改。
    """
    with get_session(request) as session:
        stored = session.get(AppSetting, "subject_key")
        raw = stored.value_json if stored is not None else None
        # chosen 的语义是「老师显式选过」：只要库里写过 subject_key 就算选过，
        # 即便写的是空串（前端会把它归一为默认学科）也不再弹窗打扰。
        chosen = stored is not None
        current = normalize_subject_key(raw)
    return {
        "subjects": list_subjects(),
        "current": current,
        "chosen": chosen,
        "default": DEFAULT_SUBJECT_KEY,
    }


@router.get("/settings", response_model=SettingsRead)
def read_settings(request: Request):
    with get_session(request) as session:
        return _stored_settings(session)


@router.patch("/settings", response_model=SettingsRead)
def update_settings(payload: SettingsPatch, request: Request):
    with get_session(request) as session:
        current = _stored_settings(session)
        values = payload.model_dump(exclude_unset=True)
        if "subject_key" in values and values["subject_key"] != current["subject_key"]:
            blocker = subject_switch_blocker(session)
            if blocker:
                raise HTTPException(
                    status_code=409,
                    detail=f"{blocker}，当前数据属于{get_subject(current['subject_key']).label}。请先在独立工作区使用另一学科，避免原数据被误读。",
                )
            previous = get_subject(current["subject_key"])
            if "subject" not in values and current.get("subject") in {
                "英语", previous.label, previous.teacher_subject_default,
            }:
                values["subject"] = get_subject(values["subject_key"]).teacher_subject_default
        try:
            SettingsRead(**{**current, **values})
        except ValidationError as error:
            raise HTTPException(422, error.errors()[0]["msg"]) from error
        for key, value in values.items():
            item = session.get(AppSetting, key)
            if item:
                item.value_json = value
            else:
                session.add(AppSetting(key=key, value_json=value))
        session.commit()
    return read_settings(request)
