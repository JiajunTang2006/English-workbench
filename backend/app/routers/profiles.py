from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from ..auth import require_token
from ..models import Student
from ..schemas import StudentProfileRead, StudentProfileUpdateRequest
from ..services.profiles import student_profile
from ..services.student_profiles import apply_profile_edit
from ..services.terms import current_term_id


router = APIRouter(prefix="/api/v1/students", tags=["profiles"], dependencies=[Depends(require_token)])


def _resolve_student_id(session, raw_student_id: str) -> int:
    """兼容前端工作台使用学号、Agent 使用数据库整数 ID 的两种引用。"""
    value = str(raw_student_id or "").strip()
    student = None
    if value.isdigit():
        student = session.get(Student, int(value))
    if student is None:
        student = session.scalar(select(Student).where(Student.student_no == value))
    if student is None:
        raise HTTPException(status_code=404, detail="学生不存在")
    return int(student.id)


@router.get("/{student_id}/profile", response_model=StudentProfileRead)
def read_profile(student_id: str, request: Request, term_id: int | None = Query(default=None, gt=0)):
    with request.app.state.session_factory() as session:
        resolved_id = _resolve_student_id(session, student_id)
        return student_profile(session, resolved_id, term_id=term_id or current_term_id(session))


@router.patch("/{student_id}/profile", response_model=StudentProfileRead)
def update_profile(
    student_id: str,
    body: StudentProfileUpdateRequest,
    request: Request,
    term_id: int | None = Query(default=None, gt=0),
):
    """教师直接修改正式画像；服务层保留 confirmed revision 审计记录。"""
    with request.app.state.session_factory() as session:
        resolved_id = _resolve_student_id(session, student_id)
        selected_term_id = term_id or current_term_id(session)
        apply_profile_edit(
            session,
            student_id=resolved_id,
            term_id=selected_term_id,
            patch=body.patch,
            expected_version=body.expected_version,
            updated_by="teacher",
        )
        return student_profile(session, resolved_id, term_id=selected_term_id)
