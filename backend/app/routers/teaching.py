"""Authenticated task/workspace APIs; all adoption and outcome writes are explicit."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update

from ..auth import require_token
from ..models import AgentSession, AnalysisRun
from ..models.teaching_entities import TeachingArtifactRevision, TeachingTask
from ..schemas.agent import AgentSessionRead
from ..schemas.teaching import (
    ArtifactCreate, ArtifactRestore, ArtifactUpdate, FeedbackCreate, ReportTaskCreate, TaskCreate, TaskUpdate,
)
from ..services import teaching as service
from ..services.subjects import get_selected_subject

router = APIRouter(prefix="/api/v1/teaching", tags=["teaching"], dependencies=[Depends(require_token)])


@router.get("/tasks")
def list_tasks(request: Request, term_id: int, include_archived: bool = False):
    with request.app.state.session_factory() as db:
        query = select(TeachingTask).where(TeachingTask.term_id == term_id,
            TeachingTask.subject_key == get_selected_subject(db).key)
        if not include_archived:
            query = query.where(TeachingTask.phase != "archived")
        return [service.task_dict(t) for t in db.scalars(query.order_by(TeachingTask.updated_at.desc(), TeachingTask.id.desc()))]


@router.post("/tasks", status_code=201)
def create_task(request: Request, payload: TaskCreate):
    with request.app.state.session_factory() as db:
        service.validate_scope(db, payload.term_id, payload.class_id, payload.exam_id)
        task = TeachingTask(term_id=payload.term_id, subject_key=get_selected_subject(db).key,
            class_id=payload.class_id, exam_id=payload.exam_id, title=payload.title,
            goal=payload.goal, constraints_json=payload.constraints.model_dump(),
            target_type=payload.target_type, student_ids_json=payload.student_ids)
        service.validate_targets(db, task, payload.target_type, payload.student_ids)
        db.add(task)
        db.commit()
        return service.task_detail(db, task)


@router.post("/tasks/from-report/{run_id}", status_code=201)
def task_from_report(request: Request, run_id: int, payload: ReportTaskCreate):
    with request.app.state.session_factory() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None:
            raise HTTPException(404, "报告运行不存在")
        if run.subject_key != get_selected_subject(db).key:
            raise HTTPException(409, "报告属于另一学科")
        service.validate_scope(db, run.term_id, run.class_id, run.exam_id)
        task = TeachingTask(term_id=run.term_id, subject_key=run.subject_key,
            class_id=run.class_id, exam_id=run.exam_id, title=payload.title,
            goal=payload.goal, constraints_json=payload.constraints.model_dump(),
            target_type="individual" if run.student_id else "class",
            student_ids_json=[run.student_id] if run.student_id else [])
        service.validate_targets(db, task, task.target_type, task.student_ids_json)
        db.add(task)
        db.flush()
        # Adoption and task creation commit together: invalid reports leave no empty task.
        service.import_report(db, task, run_id)
        db.commit()
        return service.task_detail(db, task)


@router.get("/tasks/{task_id}")
def read_task(request: Request, task_id: int):
    with request.app.state.session_factory() as db:
        return service.task_detail(db, service.require_task(db, task_id))


@router.patch("/tasks/{task_id}")
def update_task(request: Request, task_id: int, payload: TaskUpdate):
    with request.app.state.session_factory() as db:
        task = service.require_task(db, task_id)
        changes = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if task.phase in {"completed", "archived"} and set(changes) - {"phase"}:
            raise HTTPException(409, "请先重新打开教学任务再修改内容")
        if "constraints" in changes:
            changes["constraints_json"] = changes.pop("constraints")
        if "student_ids" in changes or "target_type" in changes:
            target_type = changes.get("target_type", task.target_type)
            ids = changes.pop("student_ids", task.student_ids_json)
            service.validate_targets(db, task, target_type, ids)
            changes["student_ids_json"] = ids
        result = db.execute(update(TeachingTask).where(TeachingTask.id == task.id,
            TeachingTask.revision == payload.expected_revision).values(**changes, revision=payload.expected_revision + 1))
        if result.rowcount != 1:
            raise HTTPException(409, "教学任务已更新，请刷新后重试")
        db.commit()
        db.refresh(task)
        return service.task_detail(db, task)


@router.post("/tasks/{task_id}/sessions", response_model=AgentSessionRead, status_code=201)
def task_session(request: Request, task_id: int):
    with request.app.state.session_factory() as db:
        task = service.require_task(db, task_id)
        service.validate_scope(db, task.term_id, task.class_id, task.exam_id)
        session = AgentSession(title=task.title, term_id=task.term_id, subject_key=task.subject_key,
            class_id=task.class_id, exam_id=task.exam_id, teaching_task_id=task.id, status="active",
            student_id=task.student_ids_json[0] if task.target_type == "individual" else None)
        db.add(session)
        db.commit()
        return session


@router.post("/tasks/{task_id}/artifacts", status_code=201)
def create_artifact(request: Request, task_id: int, payload: ArtifactCreate):
    with request.app.state.session_factory() as db:
        task = service.require_task(db, task_id, writable=True)
        item = service.create_artifact(db, task, payload)
        db.commit()
        return service.artifact_dict(item)


@router.put("/tasks/{task_id}/artifacts/{artifact_id}")
def save_artifact(request: Request, task_id: int, artifact_id: int, payload: ArtifactUpdate):
    with request.app.state.session_factory() as db:
        item = service.save_artifact(db, task_id, artifact_id, payload)
        db.commit()
        return service.artifact_dict(item)


@router.get("/tasks/{task_id}/artifacts/{artifact_id}/revisions")
def revisions(request: Request, task_id: int, artifact_id: int):
    with request.app.state.session_factory() as db:
        service.require_artifact(db, task_id, artifact_id)
        return [{"revision": r.revision, "content": r.content_json, "origin": r.origin,
                 "created_at": r.created_at} for r in db.scalars(select(TeachingArtifactRevision)
                 .where(TeachingArtifactRevision.artifact_id == artifact_id)
                 .order_by(TeachingArtifactRevision.revision.desc()))]


@router.post("/tasks/{task_id}/artifacts/{artifact_id}/restore")
def restore(request: Request, task_id: int, artifact_id: int, payload: ArtifactRestore):
    with request.app.state.session_factory() as db:
        service.require_artifact(db, task_id, artifact_id)
        previous = db.scalar(select(TeachingArtifactRevision).where(
            TeachingArtifactRevision.artifact_id == artifact_id,
            TeachingArtifactRevision.revision == payload.revision))
        if previous is None:
            raise HTTPException(404, "材料版本不存在")
        item = service.save_artifact(db, task_id, artifact_id,
            ArtifactUpdate(**previous.content_json, expected_revision=payload.expected_revision), "restore")
        db.commit()
        return service.artifact_dict(item)


@router.post("/tasks/{task_id}/adopt/{run_id}")
def adopt_report(request: Request, task_id: int, run_id: int):
    with request.app.state.session_factory() as db:
        task = service.require_task(db, task_id, writable=True)
        items = service.import_report(db, task, run_id)
        db.commit()
        return {"artifacts": [service.artifact_dict(a) for a in items]}


@router.post("/tasks/{task_id}/feedback", status_code=201)
def feedback(request: Request, task_id: int, payload: FeedbackCreate):
    with request.app.state.session_factory() as db:
        task = service.require_task(db, task_id, writable=True)
        item = service.add_feedback(db, task, payload)
        db.commit()
        return service.feedback_dict(item)
