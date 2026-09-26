"""验证 Harness 成绩分析工具严格遵守运行作用域中的 class_id。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.agent.tools.exam_tools import (
    _get_exam_statistics,
    _get_score_distribution,
)
from backend.app.agent.tools.tool_context import (
    ToolContext,
    reset_tool_context,
    set_tool_context,
)
from backend.app.database import Base
from backend.app.models import agent_entities, entities  # noqa: F401
from backend.app.models.entities import Class, Exam, ExamScore, Student, Term


@pytest.fixture()
def scoped_exam_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = Session(bind=engine)

    term = Term(code="scope-test", name="作用域测试学期")
    class_a = Class(term=term, name="711", grade="七年级")
    class_b = Class(term=term, name="712", grade="七年级")
    exam = Exam(
        term=term,
        source_key="scope-test-exam",
        name="作用域测试考试",
        full_score=100,
    )
    db.add_all([term, class_a, class_b, exam])
    db.flush()

    students = [
        Student(student_no="A01", name="甲一", class_id=class_a.id),
        Student(student_no="A02", name="甲二", class_id=class_a.id),
        Student(student_no="B01", name="乙一", class_id=class_b.id),
        Student(student_no="B02", name="乙二", class_id=class_b.id),
    ]
    db.add_all(students)
    db.flush()
    db.add_all([
        ExamScore(exam_id=exam.id, student_id=students[0].id, class_id_at_exam=class_a.id, total_score=80),
        ExamScore(exam_id=exam.id, student_id=students[1].id, class_id_at_exam=class_a.id, total_score=90),
        ExamScore(exam_id=exam.id, student_id=students[2].id, class_id_at_exam=class_b.id, total_score=60),
        ExamScore(exam_id=exam.id, student_id=students[3].id, class_id_at_exam=class_b.id, total_score=70),
    ])
    db.commit()

    try:
        yield db, exam.id, class_a.id, class_b.id
    finally:
        db.close()
        engine.dispose()


def _call_tool(db, scope, handler, **kwargs):
    token = set_tool_context(ToolContext(db_session=db, scope=scope))
    try:
        return handler(**kwargs)
    finally:
        reset_tool_context(token)


def test_exam_statistics_respects_selected_class(scoped_exam_db):
    db, exam_id, class_a_id, _ = scoped_exam_db

    selected_class = _call_tool(
        db,
        {"term_id": 1, "exam_id": exam_id, "class_id": class_a_id},
        _get_exam_statistics,
    )["data"]
    all_classes = _call_tool(
        db,
        {"term_id": 1, "exam_id": exam_id},
        _get_exam_statistics,
    )["data"]

    assert selected_class["class_id"] == class_a_id
    assert selected_class["participant_count"] == 2
    assert selected_class["average_score"] == 85
    assert all_classes["class_id"] is None
    assert all_classes["participant_count"] == 4
    assert all_classes["average_score"] == 75


def test_score_distribution_respects_selected_class(scoped_exam_db):
    db, exam_id, _, class_b_id = scoped_exam_db

    selected_class = _call_tool(
        db,
        {"term_id": 1, "exam_id": exam_id, "class_id": class_b_id},
        _get_score_distribution,
    )["data"]
    all_classes = _call_tool(
        db,
        {"term_id": 1, "exam_id": exam_id},
        _get_score_distribution,
    )["data"]

    assert selected_class["class_id"] == class_b_id
    assert sum(item["count"] for item in selected_class["distribution"]) == 2
    assert all_classes["class_id"] is None
    assert sum(item["count"] for item in all_classes["distribution"]) == 4
