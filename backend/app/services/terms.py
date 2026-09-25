from __future__ import annotations

from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting, Class, Enrollment, Student, Term, WorkspaceState


def current_term_id(session: Session) -> int:
    setting = session.get(AppSetting, "active_term_id")
    candidate = setting.value_json if setting else None
    try:
        candidate = int(candidate)
    except (TypeError, ValueError):
        candidate = None
    if candidate and session.get(Term, candidate):
        return candidate
    term = session.scalar(select(Term).where(Term.status == "active").order_by(Term.id))
    if term is None:
        raise HTTPException(409, "尚未创建学期")
    if setting:
        setting.value_json = term.id
    else:
        session.add(AppSetting(key="active_term_id", value_json=term.id))
    session.flush()
    return term.id


def require_term(session: Session, term_id: int, *, active_only: bool = False) -> Term:
    term = session.get(Term, term_id)
    if term is None or (active_only and term.status != "active"):
        raise HTTPException(404, "学期不存在或已归档")
    return term


def clone_workspace_state(source: WorkspaceState, *, include_roster: bool) -> dict:
    state = deepcopy(source.state_json)
    if not include_roster:
        state["classes"] = []
        state["students"] = []
    # 归档名单属于原学期，不应被带到新学期。
    state["archivedStudents"] = []
    state["exams"] = []
    state["currentExamId"] = ""
    state["recitations"] = []
    state["writings"] = []
    state["errors"] = []
    state["paperDocuments"] = []
    state["critical"] = []
    state["todos"] = []
    state["dictation"] = {}
    state["homeworkTasks"] = []
    state["homeworkRecords"] = {}
    # 成长森林的手工积分属于原学期，历史树归档为「年轮」，不拷贝成新学期活动。
    state["forest"] = {"logs": {}}
    return state
