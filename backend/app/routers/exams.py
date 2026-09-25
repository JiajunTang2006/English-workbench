from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import require_token
from ..schemas import (
    ExamClassMetricRead,
    ExamClassMetricUpsert,
    ExamCreate,
    ExamPatch,
    ExamRead,
    ExamScoreRead,
    ExamScoresUpsert,
    ExamSummary,
    ExamWorkspaceMutation,
    ExamWorkspaceMutationRead,
    StudentItemResultPatch,
    StudentItemResultRead,
)
from ..services import exams as service
from ..services.growth.exam_events import sync_exam_growth_events
from ..services.terms import current_term_id, require_term


router = APIRouter(prefix="/api/v1/exams", tags=["exams"], dependencies=[Depends(require_token)])


def get_session(request: Request):
    return request.app.state.session_factory()


def selected_term_id(session, term_id: int | None) -> int:
    return term_id or current_term_id(session)


@router.get("", response_model=list[ExamRead])
def list_exams(request: Request, include_archived: bool = False, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        require_term(session, selected_id)
        return service.list_exams(session, term_id=selected_id, include_archived=include_archived)


@router.post("", response_model=ExamRead, status_code=201)
def create_exam(payload: ExamCreate, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        require_term(session, selected_id, active_only=True)
        item = service.create_exam(session, payload, term_id=selected_id)
        session.commit()
        return service.get_exam(session, item.id, term_id=selected_id)


@router.get("/{exam_id}", response_model=ExamRead)
def read_exam(exam_id: int, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        return service.get_exam(session, exam_id, term_id=selected_term_id(session, term_id))


@router.patch("/{exam_id}", response_model=ExamRead)
def update_exam(exam_id: int, payload: ExamPatch, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        service.update_exam(session, exam_id, payload, term_id=selected_id)
        sync_exam_growth_events(session, term_id=selected_id)
        session.commit()
        return service.get_exam(session, exam_id, term_id=selected_id)


@router.post("/{exam_id}/archive", response_model=ExamWorkspaceMutationRead)
def archive_exam(exam_id: int, payload: ExamWorkspaceMutation, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        exam, state, revision = service.archive_exam(session, exam_id, term_id=selected_id, source_key=payload.source_key, expected_revision=payload.expected_revision)
        sync_exam_growth_events(session, term_id=selected_id)
        session.commit()
        return {"exam": exam, "state": state, "revision": revision}


@router.post("/{exam_id}/restore", response_model=ExamWorkspaceMutationRead)
def restore_exam(exam_id: int, payload: ExamWorkspaceMutation, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        exam, state, revision = service.restore_exam(session, exam_id, term_id=selected_id, source_key=payload.source_key, expected_revision=payload.expected_revision)
        sync_exam_growth_events(session, term_id=selected_id)
        session.commit()
        return {"exam": exam, "state": state, "revision": revision}


@router.post("/{exam_id}/purge", response_model=ExamWorkspaceMutationRead)
def purge_exam(exam_id: int, payload: ExamWorkspaceMutation, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        exam, state, revision = service.purge_exam(session, exam_id, term_id=selected_id, source_key=payload.source_key, expected_revision=payload.expected_revision)
        sync_exam_growth_events(session, term_id=selected_id)
        session.commit()
        return {"exam": exam, "state": state, "revision": revision}


@router.put("/{exam_id}/scores", response_model=list[ExamScoreRead])
def put_scores(exam_id: int, payload: ExamScoresUpsert, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        service.upsert_scores(session, exam_id, payload, term_id=selected_id)
        sync_exam_growth_events(session, term_id=selected_id)
        session.commit()
        return service.score_rows(session, exam_id, term_id=selected_id)


@router.get("/{exam_id}/scores", response_model=list[ExamScoreRead])
def list_scores(exam_id: int, request: Request, class_id: int | None = Query(default=None, gt=0), term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        return service.score_rows(session, exam_id, term_id=selected_term_id(session, term_id), class_id=class_id)


@router.get("/{exam_id}/summary", response_model=ExamSummary)
def read_summary(exam_id: int, request: Request, class_id: int | None = Query(default=None, gt=0), term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        return service.exam_summary(session, exam_id, term_id=selected_term_id(session, term_id), class_id=class_id)


@router.get("/{exam_id}/question-metrics")
def read_question_metrics(exam_id: int, request: Request, class_id: int | None = Query(default=None, gt=0), term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        return {"exam_id": exam_id, "questions": service.question_metrics(session, exam_id, term_id=selected_term_id(session, term_id), class_id=class_id)}


# --- 试卷记忆（RAG v3 · 记忆板块）---

@router.get("/{exam_id}/paper-memory")
def read_paper_memory(exam_id: int, request: Request, term_id: int | None = Query(default=None, gt=0)):
    """当前最新试卷记忆（draft 或 confirmed），无则 data 为 null。"""
    from ..services import paper_memory
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        service.get_exam(session, exam_id, term_id=selected_id)
        memory = paper_memory.get_latest_memory(session, exam_id)
        return {"exam_id": exam_id,
                "data": paper_memory.memory_payload(memory) if memory else None}


@router.post("/{exam_id}/paper-memory/generate")
def generate_paper_memory(exam_id: int, request: Request, term_id: int | None = Query(default=None, gt=0)):
    """AI 生成试卷记忆草稿（需已配置文本模型；录入试卷结构后可用）。"""
    from fastapi import HTTPException
    from ..services import paper_memory
    with get_session(request) as session:
        try:
            result = paper_memory.generate_paper_memory(
                session, exam_id, term_id=selected_term_id(session, term_id))
        except RuntimeError as exc:  # API Key 未配置等
            raise HTTPException(status_code=503, detail={
                "code": "provider_not_configured", "message": str(exc)})
        except HTTPException:
            raise
        if not result.get("ok"):
            status = 404 if result.get("error") == "paper_structure_missing" else 422
            raise HTTPException(status_code=status, detail=result)
        return result


@router.post("/{exam_id}/paper-memory/{memory_id}/confirm")
def confirm_paper_memory(exam_id: int, memory_id: int, request: Request,
                         body: dict | None = None,
                         term_id: int | None = Query(default=None, gt=0)):
    """教师确认（可带编辑后的 markdown 内容）；旧确认版本自动置为已替代。"""
    from fastapi import HTTPException
    from ..services import paper_memory
    content = (body or {}).get("content_md")
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        service.get_exam(session, exam_id, term_id=selected_id)
        result = paper_memory.confirm_paper_memory(
            session, exam_id, memory_id, content=content)
        if not result.get("ok"):
            status = 404 if result.get("error") == "memory_not_found" else 409
            raise HTTPException(status_code=status, detail=result)
        return result


@router.get("/{exam_id}/students/{student_id}/item-results", response_model=list[StudentItemResultRead])
def read_student_item_results(exam_id: int, student_id: int, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        return service.student_item_results(session, exam_id, student_id, term_id=selected_term_id(session, term_id))


@router.get("/{exam_id}/students/{student_id}/score-details")
def read_student_score_details(exam_id: int, student_id: int, request: Request,
                               term_id: int | None = Query(default=None, gt=0),
                               include_draft: bool = Query(default=False)):
    """Same version, totals, item completeness and sections used by TeachMate.

    正式诊断默认只使用已确认试卷（``include_draft=False``）；审核界面需要查看
    尚未确认的草稿结构时可显式传 ``include_draft=true``，返回体会带
    ``paper_status`` 供前端标注。
    """
    from ..services.student_score_details import get_student_score_details
    with get_session(request) as session:
        service.get_exam(session, exam_id, term_id=selected_term_id(session, term_id))
        try:
            return get_student_score_details(session, exam_id=exam_id,
                                             student_id=student_id,
                                             allow_draft=include_draft)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{exam_id}/students/{student_id}/item-results/{question_id}", response_model=StudentItemResultRead)
def patch_student_item_result(
    exam_id: int,
    student_id: int,
    question_id: int,
    payload: StudentItemResultPatch,
    request: Request,
    term_id: int | None = Query(default=None, gt=0),
):
    with get_session(request) as session:
        selected_id = selected_term_id(session, term_id)
        result = service.override_student_item_result(
            session, exam_id, student_id, question_id, payload, term_id=selected_id
        )
        session.commit()
        return result


@router.get("/{exam_id}/class-metrics/{class_id}", response_model=ExamClassMetricRead)
def read_class_metric(exam_id: int, class_id: int, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        return service.get_class_metric(session, exam_id, class_id, term_id=selected_term_id(session, term_id))


@router.put("/{exam_id}/class-metrics/{class_id}", response_model=ExamClassMetricRead)
def put_class_metric(exam_id: int, class_id: int, payload: ExamClassMetricUpsert, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with get_session(request) as session:
        result = service.upsert_class_metric(session, exam_id, class_id, payload, term_id=selected_term_id(session, term_id))
        session.commit()
        return result
