"""考试分析数据层工具测试

覆盖：get_question_difficulty（逐题难度/错误选项聚合）、
get_knowledge_coverage（knowledge_nodes_json + 逐题作答的知识点得分率）、
试卷版本 confirmed 优先策略、试卷录入知识点落库。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.agent.registry.tools import create_default_registry
from backend.app.agent.tools.tool_context import ToolContext, set_tool_context, reset_tool_context
from backend.app.database import Base
from backend.app.models import (
    Class, Exam, ExamPaperVersion, ExamQuestion, ExamScore,
    KnowledgePoint, QuestionKnowledgePoint, Student, StudentItemResult, Term,
)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/tools.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    yield s
    s.close()


def _seed(session):
    term = Term(code="T2026", name="2026-2027学年第一学期")
    session.add(term)
    session.flush()
    cls_a = Class(term_id=term.id, name="初一（1）班")
    cls_b = Class(term_id=term.id, name="初一（2）班")
    session.add_all([cls_a, cls_b])
    session.flush()
    students = [
        Student(student_no="S1", name="甲", class_id=cls_a.id),
        Student(student_no="S2", name="乙", class_id=cls_a.id),
        Student(student_no="S3", name="丙", class_id=cls_b.id),
    ]
    session.add_all(students)
    session.flush()
    exam = Exam(term_id=term.id, name="期中考试", full_score=100, source_key="t:mid")
    session.add(exam)
    session.flush()
    for student, total in zip(students, [82.0, 90.0, 70.0]):
        session.add(ExamScore(exam_id=exam.id, student_id=student.id,
                              total_score=total, class_id_at_exam=student.class_id,
                              attendance_status="present"))
    session.flush()
    return {"term": term, "cls_a": cls_a, "cls_b": cls_b,
            "students": students, "exam": exam}


def _add_paper(session, exam, version, status, questions):
    paper = ExamPaperVersion(exam_id=exam.id, version=version, status=status, full_score=100)
    session.add(paper)
    session.flush()
    for spec in questions:
        question = ExamQuestion(paper_version_id=paper.id, question_no=spec["no"],
                                question_type=spec.get("type"), max_score=spec["max"],
                                knowledge_nodes_json=spec.get("nodes") or [])
        session.add(question)
        session.flush()
    return paper


def _add_item(session, exam, student, question, *, score, correct=None, option=None,
              score_rate=None, attendance="present"):
    session.add(StudentItemResult(
        exam_id=exam.id, student_id=student.id, question_id=question.id,
        score=score, score_rate=score_rate, correct=correct,
        selected_option=option, attendance_status=attendance))
    session.flush()


def _execute(registry, session, name, **scope):
    ctx = ToolContext(db_session=session, scope=scope)
    token = set_tool_context(ctx)
    try:
        return registry.execute(name)
    finally:
        reset_tool_context(token)


def _seed_main_data(session):
    seed = _seed(session)
    exam = seed["exam"]
    s1, s2, s3 = seed["students"]
    # 更早的草稿版本：含干扰题目"99"，验证 confirmed 优先
    _add_paper(session, exam, version=1, status="draft",
               questions=[{"no": "99", "max": 10}])
    confirmed = _add_paper(session, exam, version=2, status="confirmed", questions=[
        {"no": "1", "max": 10, "type": "choice", "nodes": ["宾语从句"]},
        {"no": "2", "max": 5, "type": "choice", "nodes": ["宾语从句", "从句语序"]},
    ])
    q1, q2 = confirmed.questions
    _add_item(session, exam, s1, q1, score=3, correct=False, option="C")
    _add_item(session, exam, s2, q1, score=8, correct=True, option="A")
    _add_item(session, exam, s3, q1, score=10, correct=True, option="A")
    _add_item(session, exam, s1, q2, score=5, correct=True, option="A")
    _add_item(session, exam, s2, q2, score=5, correct=True, option="B")
    session.flush()
    seed.update({"confirmed": confirmed, "q1": q1, "q2": q2})
    return seed


def test_question_difficulty_uses_confirmed_version_and_aggregates(session):
    seed = _seed_main_data(session)
    registry = create_default_registry()
    result = _execute(registry, session, "get_question_difficulty",
                      exam_id=seed["exam"].id, term_id=seed["term"].id)
    data = result["data"]
    assert data["paper_status"] == "confirmed"
    assert data["version"] == 2
    rows = {row["question_no"]: row for row in data["questions"]}
    assert "99" not in rows
    # 全年级口径：q1 三人得分率 (0.3+0.8+1.0)/3
    assert rows["1"]["score_rate"] == pytest.approx(0.7, abs=1e-4)
    assert rows["1"]["wrong_options"] == [{"option": "C", "count": 1}]
    assert rows["1"]["knowledge_nodes"] == ["宾语从句"]
    assert rows["1"]["full_mark_count"] == 1
    # 班级口径：只算初一（1）班
    scoped = _execute(registry, session, "get_question_difficulty",
                      exam_id=seed["exam"].id, term_id=seed["term"].id,
                      class_id=seed["cls_a"].id)
    row = {r["question_no"]: r for r in scoped["data"]["questions"]}["1"]
    assert row["score_rate"] == pytest.approx(0.55, abs=1e-4)
    assert row["participant_count"] == 2


def test_question_difficulty_draft_fallback_and_no_paper(session):
    seed = _seed(session)
    registry = create_default_registry()
    # 无任何试卷结构
    empty = _execute(registry, session, "get_question_difficulty",
                     exam_id=seed["exam"].id, term_id=seed["term"].id)
    assert empty["data"]["questions"] == []
    assert "尚未录入试卷结构" in empty["data"]["note"]
    # 只有草稿：回退使用但显式标注
    paper = _add_paper(session, seed["exam"], version=1, status="draft",
                       questions=[{"no": "1", "max": 10}])
    result = _execute(registry, session, "get_question_difficulty",
                      exam_id=seed["exam"].id, term_id=seed["term"].id)
    assert result["data"]["paper_status"] == "draft"
    assert "未确认" in result["data"]["note"]
    assert result["data"]["questions"][0]["question_no"] == "1"
    assert result["data"]["questions"][0]["scored_count"] == 0
    session.delete(paper)
    session.flush()


def test_knowledge_coverage_weighted_rates(session):
    seed = _seed_main_data(session)
    registry = create_default_registry()
    result = _execute(registry, session, "get_knowledge_coverage",
                      exam_id=seed["exam"].id, term_id=seed["term"].id,
                      class_id=seed["cls_a"].id)
    data = result["data"]
    assert data["questions_tagged"] == 2
    assert data["participant_count"] == 2
    points = {p["name"]: p for p in data["knowledge_points"]}
    # 宾语从句：q1（10 分，0.55）+ q2（5 分，1.0）→ 分值加权 (5.5+5)/15
    assert points["宾语从句"]["avg_score_rate"] == pytest.approx(0.7, abs=1e-4)
    assert points["宾语从句"]["question_count"] == 2
    assert points["宾语从句"]["total_score"] == pytest.approx(15.0)
    # 从句语序只在 q2（得分率 1.0），排序应把薄弱的排前
    assert points["从句语序"]["avg_score_rate"] == pytest.approx(1.0)
    rates = [p["avg_score_rate"] for p in data["knowledge_points"]]
    assert rates == sorted([r for r in rates if r is not None], reverse=False)


def test_knowledge_coverage_unions_structured_links(session):
    seed = _seed_main_data(session)
    kp = KnowledgePoint(code="kp-obj-clause", name="宾语从句（词表）", level=2)
    session.add(kp)
    session.flush()
    session.add(QuestionKnowledgePoint(question_id=seed["q1"].id,
                                       knowledge_point_id=kp.id, is_primary=True))
    session.flush()
    registry = create_default_registry()
    result = _execute(registry, session, "get_knowledge_coverage",
                      exam_id=seed["exam"].id, term_id=seed["term"].id,
                      class_id=seed["cls_a"].id)
    names = {p["name"] for p in result["data"]["knowledge_points"]}
    assert {"宾语从句", "从句语序", "宾语从句（词表）"} <= names


def test_knowledge_coverage_without_tags_notes(session):
    seed = _seed(session)
    _add_paper(session, seed["exam"], version=1, status="confirmed",
               questions=[{"no": "1", "max": 10, "nodes": []}])
    registry = create_default_registry()
    result = _execute(registry, session, "get_knowledge_coverage",
                      exam_id=seed["exam"].id, term_id=seed["term"].id)
    data = result["data"]
    assert data["knowledge_points"] == []
    assert data["questions_tagged"] == 0
    assert "尚未标注知识点" in data["note"]


def test_question_list_reports_paper_status(session):
    seed = _seed_main_data(session)
    registry = create_default_registry()
    result = _execute(registry, session, "get_question_list",
                      exam_id=seed["exam"].id, term_id=seed["term"].id)
    assert result["data"]["paper_status"] == "confirmed"
    assert {q["question_no"] for q in result["data"]["questions"]} == {"1", "2"}


def test_ingestion_persists_knowledge_points(session):
    seed = _seed(session)
    from backend.app.services.exam_ingestion import ExamIngestionService
    paper = _add_paper(session, seed["exam"], version=1, status="draft", questions=[])
    ExamIngestionService._add_question(paper, {
        "question_no": "12", "max_score": 2, "question_type": "choice",
        "knowledge_points": [" 宾语从句 ", "宾语从句", "定语从句"],
    })
    session.flush()
    question = paper.questions[0]
    assert question.knowledge_nodes_json == ["宾语从句", "定语从句"]
    payload = ExamIngestionService._paper_payload(paper)
    assert payload["questions"][0]["knowledge_points"] == ["宾语从句", "定语从句"]
