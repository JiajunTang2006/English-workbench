"""Authenticated teacher-only practice APIs. No new student-facing surface."""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select
from ..auth import require_token
from ..models.teaching_entities import PracticeAttempt, PracticeQuestion, PracticeSet
from ..schemas.practice import (AttemptBatch, AttemptCorrection, AttemptCreate, FeedbackGenerate,
    PracticeConfirm, PracticeDraft, PracticeGenerate, PracticeReuse, QuestionDraft)
from ..services import practice as service
from ..services.teaching import require_task

router = APIRouter(prefix="/api/v1/teaching/tasks", tags=["practice"], dependencies=[Depends(require_token)])


def require_plugin(request: Request):
    from .plugins import _manager
    plugin = _manager(request).get_plugin("targeted_practice")
    if plugin is None or not plugin["enabled"]:
        raise HTTPException(409, "请在插件中启用专项推题")


@router.get("/{task_id}/practice/sources")
def source_questions(request: Request, task_id: int, student_ids: list[int] = Query(default=[])):
    from ..services.practice_sources import candidates
    require_plugin(request)
    with request.app.state.session_factory() as db:
        task = require_task(db, task_id)
        ids = student_ids or task.student_ids_json
        if not ids:
            from ..services.agent_analysis.conversation import roster
            ids = [r["id"] for r in roster(db, {"term_id": task.term_id, "class_id": task.class_id})][:100]
        return candidates(db, task, ids) if ids else {"questions": [], "note": "请选择学生"}


@router.post("/{task_id}/practice/reuse", status_code=201)
async def reuse_questions(request: Request, task_id: int, payload: PracticeReuse):
    from ..services.practice_sources import reuse
    require_plugin(request)
    with request.app.state.session_factory() as db:
        item = await reuse(db, require_task(db, task_id, True), payload)
        return service.practice_dict(db, item)


@router.post("/{task_id}/practices/{practice_id}/questions/{question_id}/revise", status_code=201)
def revise_question(request: Request, task_id: int, practice_id: int, question_id: int, payload: QuestionDraft):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        task, item = service.require_practice(db, task_id, practice_id, True)
        old = service.questions(db, item)
        if question_id not in {q.id for q in old}:
            raise HTTPException(404, "题目不属于当前练习")
        if payload.source:
            raise HTTPException(422, "修改题目后不能继续声明为未经改动的原题")
        rows = [payload.model_dump() if q.id == question_id else q.content_json for q in old]
        draft = PracticeDraft.model_validate({"title": item.title[:180] + "（教师修订）", "objective": item.objective,
            "student_ids": item.student_ids_json, "level": item.level, "questions": rows})
        new = service.create_draft(db, task, draft, verified_source=True)
        changed_position = next(q.position for q in old if q.id == question_id)
        for fresh, previous in zip(service.questions(db, new), old):
            fresh.version = previous.version + (1 if previous.id == question_id else 0)
        new.constraints_json = {**new.constraints_json, "revision_of": item.id, "changed_question_position": changed_position}
        db.commit()
        return service.practice_dict(db, new)


@router.get("/{task_id}/practice/weaknesses")
def weaknesses(request: Request, task_id: int):
    with request.app.state.session_factory() as db:
        task = require_task(db, task_id)
        ids = task.student_ids_json
        if not ids:
            from ..services.agent_analysis.conversation import roster
            ids = [r["id"] for r in roster(db, {"term_id": task.term_id, "class_id": task.class_id})][:100]
        return service.weakness_context(db, task, ids) if ids else {"students": [], "note": "当前范围没有有效学生"}


@router.post("/{task_id}/practice/estimate")
def estimate(request: Request, task_id: int, payload: PracticeGenerate):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        task = require_task(db, task_id, True)
        _, messages = service.generation_messages(db, task, payload)
        import json
        return service.model_estimate(service.get_agent_config(), json.dumps(messages, ensure_ascii=False))


@router.post("/{task_id}/practice/generate", status_code=201)
async def generate(request: Request, task_id: int, payload: PracticeGenerate):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        item = await service.generate(db, require_task(db, task_id, True), payload)
        return service.practice_dict(db, item)


@router.post("/{task_id}/practices", status_code=201)
def create(request: Request, task_id: int, payload: PracticeDraft):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        item = service.create_draft(db, require_task(db, task_id, True), payload)
        db.commit()
        return service.practice_dict(db, item)


@router.get("/{task_id}/practices/{practice_id}")
def read(request: Request, task_id: int, practice_id: int, student_copy: bool = False):
    with request.app.state.session_factory() as db:
        _, item = service.require_practice(db, task_id, practice_id)
        data = service.practice_dict(db, item, student_copy=student_copy)
        if not student_copy:
            data["attempts"] = [service.attempt_dict(a) for a in db.scalars(select(PracticeAttempt)
                .where(PracticeAttempt.practice_id == item.id).order_by(PracticeAttempt.id))]
        return data


@router.post("/{task_id}/practices/{practice_id}/confirm")
def confirm(request: Request, task_id: int, practice_id: int, payload: PracticeConfirm):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        _, item = service.require_practice(db, task_id, practice_id, True)
        if not payload.checked_answers or not payload.checked_scope:
            raise HTTPException(422, "采用前请核对答案解法、实际难度和已教范围")
        item.status = "confirmed"
        item.checks_json = {**item.checks_json, "answer_correctness": "teacher_checked",
            "difficulty": "teacher_checked", "curriculum_scope": "teacher_checked"}
        db.commit()
        return service.practice_dict(db, item)


@router.post("/{task_id}/practices/{practice_id}/attempts", status_code=201)
def attempt(request: Request, task_id: int, practice_id: int, payload: AttemptCreate):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        task, item = service.require_practice(db, task_id, practice_id, True)
        row = service.add_attempt(db, task, item, payload); db.commit()
        return service.attempt_dict(row)


@router.post("/{task_id}/practices/{practice_id}/attempts/batch", status_code=201)
def attempts_batch(request: Request, task_id: int, practice_id: int, payload: AttemptBatch):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        task, item = service.require_practice(db, task_id, practice_id, True)
        rows = [service.add_attempt(db, task, item, row) for row in payload.rows]
        db.commit()
        return [service.attempt_dict(row) for row in rows]


def require_attempt(db, task_id, practice_id, attempt_id):
    task, practice = service.require_practice(db, task_id, practice_id, True)
    row = db.get(PracticeAttempt, attempt_id)
    if row is None or row.practice_id != practice.id:
        raise HTTPException(404, "作答不属于这份练习")
    return task, row


@router.patch("/{task_id}/practices/{practice_id}/attempts/{attempt_id}")
def correct(request: Request, task_id: int, practice_id: int, attempt_id: int, payload: AttemptCorrection):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        _, row = require_attempt(db, task_id, practice_id, attempt_id)
        service.correct_attempt(db, row, payload); db.commit()
        return service.attempt_dict(row)


@router.post("/{task_id}/practices/{practice_id}/attempts/{attempt_id}/feedback")
async def feedback(request: Request, task_id: int, practice_id: int, attempt_id: int, payload: FeedbackGenerate):
    require_plugin(request)
    with request.app.state.session_factory() as db:
        task, row = require_attempt(db, task_id, practice_id, attempt_id)
        question = db.get(PracticeQuestion, row.question_id)
        await service.feedback_suggestion(db, task, question, row, payload)
        return service.attempt_dict(row)
