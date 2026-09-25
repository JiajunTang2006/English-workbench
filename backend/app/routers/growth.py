"""成长树 API（方案 §5.2）。

路由层只做范围校验、输入转换与输出契约；计分公式只在
``services/growth/rules.py`` 里写一份，模型与前端都不得复制第二份。

拟提供：班级森林快照 GET、学生明细 GET、教师活动批量 POST、误录撤销 POST、
旧版导入预览 POST、确认导入 POST、整批撤销 POST。所有写接口校验 term_id 与
学生归属，带幂等键；读取接口只读，不产生副作用。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..auth import require_token
from ..schemas.growth import (
    GrowthActivityBatch,
    GrowthLegacyBatchReverseRequest,
    GrowthLegacyConfirmRequest,
    GrowthLegacyPreviewRequest,
    GrowthReversalRequest,
)
from ..services.growth import legacy_import, service
from ..services.growth.service import GrowthScopeError
from ..services.terms import current_term_id, require_term

router = APIRouter(prefix="/api/v1/growth", tags=["growth"], dependencies=[Depends(require_token)])


def _session(request: Request) -> Session:
    return request.app.state.session_factory()


def _resolve_term(session: Session, term_id: int | None) -> int:
    """未指定时取当前学期；显式指定时必须存在且未归档。"""
    if term_id is None:
        return current_term_id(session)
    require_term(session, term_id, active_only=True)
    return term_id


@router.get("/forest")
def read_forest(
    request: Request,
    term_id: int | None = Query(default=None, gt=0),
    class_id: int | None = Query(default=None, gt=0),
):
    """班级森林页数据：每名学生一张卡片所需的全部数值。"""
    with _session(request) as session:
        resolved = _resolve_term(session, term_id)
        return service.forest_rows(session, term_id=resolved, class_id=class_id)


@router.get("/students/{student_id}")
def read_student_detail(
    student_id: int,
    request: Request,
    term_id: int | None = Query(default=None, gt=0),
    class_id: int | None = Query(default=None, gt=0),
):
    """单人成长明细：阶段、来源明细、五个分支、待订正与历史年轮。"""
    with _session(request) as session:
        resolved = _resolve_term(session, term_id)
        try:
            return service.student_detail(
                session, term_id=resolved, student_id=student_id, class_id=class_id)
        except GrowthScopeError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("/activities", status_code=201)
def record_activities(payload: GrowthActivityBatch, request: Request):
    """教师批量补录学习事件；同一 request_id 重试不重复计分。"""
    if not payload.items:
        raise HTTPException(422, "补录列表为空")
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        result = service.record_batch(
            session,
            term_id=resolved,
            items=[item.model_dump() for item in payload.items],
            request_id=payload.request_id,
        )
        session.commit()
        result["term_id"] = resolved
        return result


@router.post("/events/{event_id}/reverse", status_code=201)
def reverse_event(event_id: int, payload: GrowthReversalRequest, request: Request):
    """撤销一条误录事件：追加引用原事件的撤销事件，不删除原记录。"""
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        try:
            result = service.reverse_event(
                session, term_id=resolved, event_id=event_id, reason=payload.reason)
        except GrowthScopeError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        result["term_id"] = resolved
        return result


@router.post("/legacy/preview")
def legacy_preview(payload: GrowthLegacyPreviewRequest, request: Request):
    """旧版（Windows 参考包）记录导入预览：只读，不写库。"""
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        return legacy_import.preview_import(session, term_id=resolved, payload=payload.payload)


@router.post("/legacy/confirm", status_code=201)
def legacy_confirm(payload: GrowthLegacyConfirmRequest, request: Request):
    """确认导入旧记录为历史营养；幂等，可整批撤销。"""
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        result = legacy_import.confirm_import(
            session,
            term_id=resolved,
            payload=payload.payload,
            batch_id=payload.batch_id,
            carry_over_date=payload.carry_over_date,
        )
        session.commit()
        result["term_id"] = resolved
        return result


@router.post("/legacy/{batch_id}/reverse")
def legacy_reverse(batch_id: str, payload: GrowthLegacyBatchReverseRequest, request: Request):
    """整批撤销一次旧版导入。"""
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        result = legacy_import.reverse_batch(
            session, term_id=resolved, batch_id=batch_id, reason=payload.reason)
        session.commit()
        result["term_id"] = resolved
        return result
