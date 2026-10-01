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
    GrowthTeacherCreate,
    GrowthTeacherPresetsUpdate,
    GrowthTeacherRename,
)
from ..services.growth import legacy_import, service, teachers
from ..services.growth import events as growth_events
from ..services.growth.service import GrowthScopeError
from ..services.growth.teachers import TeacherScopeError
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
    """教师批量补录学习事件；同一 request_id 重试不重复计分。

    操作人取当前教师档案：事件账本记下真实姓名，便于事后核对
    「这条分是谁按哪套标准加的」。
    """
    if not payload.items:
        raise HTTPException(422, "补录列表为空")
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        teacher = teachers.ensure_active_teacher(session)
        result = service.record_batch(
            session,
            term_id=resolved,
            items=[item.model_dump() for item in payload.items],
            actor=teacher.name,
            actor_id=teacher.id,
            request_id=payload.request_id,
        )
        session.commit()
        result["term_id"] = resolved
        result["teacher"] = teachers.teacher_payload(session, teacher)
        return result


@router.post("/events/{event_id}/reverse", status_code=201)
def reverse_event(event_id: int, payload: GrowthReversalRequest, request: Request):
    """撤销一条误录事件：追加引用原事件的撤销事件，不删除原记录。"""
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        teacher = teachers.ensure_active_teacher(session)
        try:
            result = service.reverse_event(
                session, term_id=resolved, event_id=event_id,
                reason=payload.reason, actor=teacher.name)
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


# ---------- 教师档案与「每位老师自己那套补录加分标准」 ----------
# 单机软件没有账号体系，这里只做「本机有哪几位老师、当前是谁」。预设只决定
# 快捷按钮与默认分值，不进入计分引擎：历史事件仍按发生当时的分值入账。


def _teacher_list_payload(session) -> dict:
    teachers_rows = teachers.list_teachers(session)
    active = teachers.get_active_teacher(session)
    return {
        "teachers": [
            {"id": row.id, "name": row.name,
             "is_active": bool(active and active.id == row.id)}
            for row in teachers_rows
        ],
        "active_teacher": teachers.teacher_payload(session, active),
        "max_points": teachers.MAX_MANUAL_POINTS,
    }


def _standard_payload(session, term_id: int) -> dict:
    """设置页一次拿全：教师列表、当前教师、生效预设、分值上限与分叉提示。"""
    active = teachers.get_active_teacher(session)
    rule = growth_events.read_rule_version(session, term_id=term_id)
    return {
        "term_id": term_id,
        "rule_version": rule.code,
        "teachers": [
            {"id": row.id, "name": row.name,
             "is_active": bool(active and active.id == row.id)}
            for row in teachers.list_teachers(session)
        ],
        "active_teacher": teachers.teacher_payload(session, active),
        "presets": teachers.resolve_presets(session, teacher=active, term_id=term_id),
        "max_points": teachers.MAX_MANUAL_POINTS,
        "conflict": teachers.presets_conflict(session, term_id=term_id),
    }


@router.get("/teachers")
def list_teachers(request: Request):
    """本机教师档案列表与当前教师（只读，不创建记录）。"""
    with _session(request) as session:
        return _teacher_list_payload(session)


@router.post("/teachers", status_code=201)
def create_teacher(payload: GrowthTeacherCreate, request: Request):
    """新建本机教师档案；默认顺带切换为当前教师。"""
    with _session(request) as session:
        try:
            teacher = teachers.create_teacher(
                session, name=payload.name, presets=payload.presets)
            if payload.activate:
                teachers.activate_teacher(session, teacher_id=teacher.id)
        except TeacherScopeError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return _teacher_list_payload(session)


@router.put("/teachers/{teacher_id}")
def rename_teacher(teacher_id: int, payload: GrowthTeacherRename, request: Request):
    """重命名教师档案。"""
    with _session(request) as session:
        try:
            teachers.rename_teacher(session, teacher_id=teacher_id, name=payload.name)
        except TeacherScopeError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return _teacher_list_payload(session)


@router.post("/teachers/{teacher_id}/activate")
def activate_teacher(teacher_id: int, request: Request):
    """切换当前教师；后续补录按该教师的预设与姓名记录。"""
    with _session(request) as session:
        try:
            teachers.activate_teacher(session, teacher_id=teacher_id)
        except TeacherScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        session.commit()
        return _teacher_list_payload(session)


@router.delete("/teachers/{teacher_id}")
def delete_teacher(teacher_id: int, request: Request):
    """删除教师档案；至少保留一位，删除当前教师时自动顺延。"""
    with _session(request) as session:
        try:
            teachers.delete_teacher(session, teacher_id=teacher_id)
        except TeacherScopeError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return _teacher_list_payload(session)


@router.get("/teacher-presets")
def read_teacher_presets(request: Request, term_id: int | None = Query(default=None, gt=0)):
    """当前教师生效的补录预设（出厂默认 + 该教师覆盖），以及多套标准是否已分叉。"""
    with _session(request) as session:
        resolved = _resolve_term(session, term_id)
        return _standard_payload(session, resolved)


@router.put("/teacher-presets")
def update_teacher_presets(payload: GrowthTeacherPresetsUpdate, request: Request,
                           teacher_id: int | None = Query(default=None, gt=0)):
    """覆盖某位教师（默认当前教师）的补录预设。

    分值范围与类别合法性按当前学期的规则版本校验：未知类别或越界分值返回 422，
    而不是静默忽略——静默忽略会让老师以为设置生效了。
    """
    with _session(request) as session:
        resolved = _resolve_term(session, payload.term_id)
        # 显式指定 teacher_id 时只改那位老师的预设，不顺带切换当前教师——
        # 「改谁的设置」与「现在是谁在操作」是两件事，隐式切换会让署名与预设错位。
        if teacher_id is not None:
            teacher = teachers.get_teacher(session, teacher_id)
            if teacher is None:
                raise HTTPException(404, "教师档案不存在")
        else:
            teacher = teachers.ensure_active_teacher(session)
        try:
            teachers.update_presets(
                session, teacher_id=teacher.id, presets=payload.presets, term_id=resolved)
        except TeacherScopeError as exc:
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        return _standard_payload(session, resolved)
