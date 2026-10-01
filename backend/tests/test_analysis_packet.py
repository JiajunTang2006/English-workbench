"""分析包预计算服务测试（RAG v3 · P1）"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.agent.analysis_packet import (
    build_packet, packet_to_text, tool_policy_for,
)
from backend.app.routers.agent import _resolve_review_plan_student
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.tools.tool_context import ToolContext, set_tool_context
from backend.app.database import Base
from backend.app.models import (
    AnalysisEvidence, AnalysisRun, Class, Enrollment, Exam, ExamPaperVersion,
    ExamQuestion, ExamScore, Student, StudentItemResult, Term,
)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/packet.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def seed(db):
    term = Term(code="T1", name="学期")
    db.add(term)
    db.flush()
    cls = Class(term_id=term.id, name="初三（1）班")
    db.add(cls)
    db.flush()
    students = [
        Student(student_no="S1", name="张三", class_id=cls.id),
        Student(student_no="S2", name="李四", class_id=cls.id),
    ]
    db.add_all(students)
    db.flush()
    exam = Exam(term_id=term.id, name="期中考试", full_score=100, source_key="t:mid")
    db.add(exam)
    db.flush()
    for student, total in zip(students, [82.0, 58.0]):
        db.add(ExamScore(exam_id=exam.id, student_id=student.id, total_score=total,
                         class_id_at_exam=cls.id, attendance_status="present"))
    run = AnalysisRun(session_id=1, term_id=term.id, capability="exam_analysis",
                      status="running")
    db.add(run)
    paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed",
                             full_score=100)
    db.add(paper)
    db.flush()
    q1 = ExamQuestion(paper_version_id=paper.id, question_no="1",
                      question_type="语法填空", max_score=10,
                      knowledge_nodes_json=["宾语从句"])
    q2 = ExamQuestion(paper_version_id=paper.id, question_no="2",
                      question_type="语法填空", max_score=5,
                      knowledge_nodes_json=["定语从句"])
    db.add_all([q1, q2])
    db.flush()
    items = [
        (students[0], q1, 3.0, "C"),
        (students[0], q2, 5.0, "A"),
        (students[1], q1, 2.0, "C"),
        (students[1], q2, 4.0, "A"),
    ]
    for student, question, score, option in items:
        db.add(StudentItemResult(
            exam_id=exam.id, student_id=student.id, question_id=question.id,
            score=score, selected_option=option, correct=score >= question.max_score,
            attendance_status="present"))
    db.add(Enrollment(term_id=term.id, class_id=cls.id,
                      student_id=students[0].id, status="active"))
    db.add(Enrollment(term_id=term.id, class_id=cls.id,
                      student_id=students[1].id, status="active"))
    db.commit()
    return {"term": term, "cls": cls, "students": students, "exam": exam,
            "run": run, "q1": q1, "q2": q2}


def _scope(seed, **overrides):
    scope = {
        "run_id": seed["run"].id, "term_id": seed["term"].id,
        "class_id": seed["cls"].id, "exam_id": seed["exam"].id,
        "student_id": None,
    }
    scope.update(overrides)
    return scope


def test_exam_packet_diagnostics_and_evidence(db, seed):
    packet = build_packet(db, "exam_analysis", _scope(seed))
    assert packet is not None
    assert packet["metrics"]["exam_name"] == "期中考试"
    kinds = {(d["kind"], d["target"]) for d in packet["diagnostics"]}
    # 薄弱知识点：宾语从句加权得分率 0.25
    assert ("knowledge_point", "宾语从句") in kinds
    # 薄弱题目：第 1 题得分率 0.25，且带题型路由的错因候选
    question_diag = next(d for d in packet["diagnostics"]
                         if d["kind"] == "question")
    assert question_diag["error_cause_candidates"] == ["语法", "审题", "词汇"]
    assert question_diag["confidence"] == "low"
    assert any("高频错误选项" in d["signal"] for d in packet["diagnostics"]
               if d["kind"] == "question")
    assert any("低置信度" in text for text in packet["limitations"])
    # 证据预登记：数据证据 + 教学依据，且都归属当前 run
    rows = db.scalars(select(AnalysisEvidence).where(
        AnalysisEvidence.run_id == seed["run"].id)).all()
    types = {row.evidence_type for row in rows}
    assert "computed_metric" in types
    assert "teaching_reference" in types
    referenced = {evid for d in packet["diagnostics"]
                  for evid in d["evidence_ids"]}
    stored = {row.evidence_id for row in rows}
    assert referenced <= stored, "诊断引用的 evidence 必须已落库"


def test_general_chat_never_builds_packet(db, seed):
    assert build_packet(db, "general_chat", _scope(seed)) is None
    expected = ["get_practice_context", "get_student_learning_evidence", "get_original_question", "resolve_student", "get_exam_overview", "get_score_distribution",
                "get_question_list", "get_wrong_questions", "get_student_scores", "get_student_trend"]
    assert tool_policy_for("general_chat", has_packet=False) == expected
    assert tool_policy_for("general_chat", has_packet=True) == expected
    assert not any("submit" in name or "update" in name for name in expected)


def test_named_review_plan_uses_verified_individual_score(db, seed):
    student = seed["students"][1]
    scope = _scope(seed, student_id=None)
    assert _resolve_review_plan_student(
        db, scope, "为李四制定复习计划") == student.id
    scope["student_id"] = student.id

    packet = build_packet(db, "review_plan", scope)
    assert packet is not None
    assert packet["scope"]["student_id"] == student.id
    assert packet["metrics"]["student_name"] == "李四"
    assert packet["metrics"]["student_total_score"] == 58.0
    assert any("李四已在本次考试成绩表中" in item["signal"]
               for item in packet["priorities"])
    assert any(item["target"] == "个人相对低分板块"
               and "语法填空" in item["signal"]
               for item in packet["priorities"])
    text = packet_to_text(packet)
    assert "李四" in text
    assert "58" in text
    assert any(item["target"].startswith("班级共性·")
               for item in packet["priorities"])
    assert _resolve_review_plan_student(
        db, _scope(seed), "为张三和李四制定复习计划") is None


def test_named_review_plan_keeps_total_score_when_item_scores_are_missing(db, seed):
    student = seed["students"][1]
    db.query(StudentItemResult).filter(
        StudentItemResult.student_id == student.id).delete()
    packet = build_packet(db, "review_plan", _scope(seed, student_id=student.id))
    text = packet_to_text(packet)
    assert "李四已在本次考试成绩表中" in text
    assert "58" in text
    assert "没有个人逐题作答成绩" in text
    assert "不能据班级风险名单" in text


def test_named_student_without_exam_score_gets_precise_error(db, seed):
    student = Student(student_no="S3", name="韩同学", class_id=seed["cls"].id)
    db.add(student)
    db.flush()
    db.add(Enrollment(term_id=seed["term"].id, class_id=seed["cls"].id,
                      student_id=student.id, status="active"))
    db.flush()
    with pytest.raises(HTTPException) as error:
        _resolve_review_plan_student(db, _scope(seed), "为韩同学制定复习计划")
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "STUDENT_EXAM_SCORE_MISSING"


def test_student_score_details_prefer_confirmed_version(db, seed):
    from backend.app.services.student_score_details import get_student_score_details

    draft = ExamPaperVersion(exam_id=seed["exam"].id, version=2, status="draft",
                             full_score=100)
    db.add(draft)
    db.flush()
    db.add(ExamQuestion(paper_version_id=draft.id, question_no="99",
                        max_score=100))
    db.commit()

    details = get_student_score_details(
        db, exam_id=seed["exam"].id,
        student_id=seed["students"][0].id,
        class_id=seed["cls"].id,
    )
    assert details["paper_version"] == 1
    assert details["detail_status"] == "complete"
    assert details["scored_items"] == details["expected_items"] == 2
    assert {item["question_no"] for item in details["item_scores"]} == {"1", "2"}
    assert details["section_scores"][0]["score"] == 8


def test_report_marks_old_item_scores_stale(db, seed):
    from backend.app.services.agent_analysis.report_snapshot import (
        ensure_report_scope_snapshot, report_freshness,
    )

    run = seed["run"]
    run.exam_id = seed["exam"].id
    run.class_id = seed["cls"].id
    ensure_report_scope_snapshot(db, run)
    db.commit()
    assert report_freshness(db, run) == "current"

    item = db.scalar(select(StudentItemResult).where(
        StudentItemResult.student_id == seed["students"][0].id,
        StudentItemResult.question_id == seed["q1"].id,
    ))
    item.score = 4.0
    item.teacher_override = True
    db.commit()
    assert report_freshness(db, run) == "stale"


def test_report_marks_knowledge_tag_change_stale(db, seed):
    """回归（评审 P2）：知识点标签也是诊断输入，改标签后旧报告必须过期。

    旧指纹只覆盖题目结构与分数，改知识点标签仍返回 current，但知识点结论
    可能已经不适用。指纹现在必须绑定逐题语义与知识点关联。
    """
    from backend.app.models import KnowledgePoint, QuestionKnowledgePoint
    from backend.app.services.agent_analysis.report_snapshot import (
        ensure_report_scope_snapshot, report_freshness,
    )

    run = seed["run"]
    run.exam_id = seed["exam"].id
    run.class_id = seed["cls"].id
    ensure_report_scope_snapshot(db, run)
    db.commit()
    assert report_freshness(db, run) == "current"

    # 只改题目的知识点标签（不动分数、不动结构）。
    seed["q1"].knowledge_nodes_json = ["状语从句"]
    db.commit()
    assert report_freshness(db, run) == "stale"

    # 恢复后再建立新快照，验证关联表变化同样能使报告过期。
    seed["q1"].knowledge_nodes_json = ["宾语从句"]
    db.commit()
    run.input_summary_json = {}
    ensure_report_scope_snapshot(db, run)
    db.commit()
    assert report_freshness(db, run) == "current"

    point = KnowledgePoint(level=3, code="grammar-attr-clause", name="定语从句")
    db.add(point)
    db.flush()
    db.add(QuestionKnowledgePoint(question_id=seed["q1"].id,
                                  knowledge_point_id=point.id, source="teacher",
                                  confirmed_by_teacher=True))
    db.commit()
    assert report_freshness(db, run) == "stale"



def test_review_packet_priorities(db, seed):
    packet = build_packet(db, "review_plan", _scope(seed))
    assert packet is not None and packet["priorities"]
    assert any(p["target"] == "宾语从句" for p in packet["priorities"][:-1])
    assert packet["priorities"][-1]["target"] == "计划骨架"


def test_student_packet_with_trend(db, seed):
    s1 = seed["students"][0]
    seed["exam"].exam_date = date(2026, 5, 1)
    exam2 = Exam(term_id=seed["term"].id, name="第一次月考", exam_date=date(2026, 4, 1), full_score=150,
                 source_key="t:m1")
    db.add(exam2)
    db.flush()
    db.add(ExamScore(exam_id=exam2.id, student_id=s1.id, total_score=135.0,
                     class_id_at_exam=seed["cls"].id,
                     attendance_status="present"))
    future = Exam(term_id=seed["term"].id, name="期末考试", exam_date=date(2026, 6, 1),
                  full_score=100, source_key="t:final")
    db.add(future)
    db.flush()
    db.add(ExamScore(exam_id=future.id, student_id=s1.id, total_score=100.0,
                     class_id_at_exam=seed["cls"].id, attendance_status="present"))
    db.commit()
    packet = build_packet(db, "student_diagnosis",
                          _scope(seed, student_id=s1.id))
    assert packet is not None
    trend = next(d for d in packet["diagnostics"] if d["kind"] == "trend")
    assert "期中考试" in trend["signal"]
    assert "第一次月考 90.0%" in trend["signal"]
    assert "期末考试" not in trend["signal"]
    assert any(d["kind"] == "question" for d in packet["diagnostics"])
    assert any("低置信度" in text for text in packet["limitations"])


def test_packet_text_sanitizes_student_name(db, seed):
    packet = build_packet(db, "student_diagnosis",
                          _scope(seed, student_id=seed["students"][0].id))
    text = packet_to_text(packet)
    # 隐私默认：分析包不含学生真实姓名（模型侧只见"该生"）
    assert "张三" not in text
    assert "该生" in text


def test_teacher_score_override_recomputes_derived_fields(db, seed):
    """回归（评审 P1）：教师改分后 score_rate / correct 必须同步重算，
    否则知识点分析与错题判定仍用旧值。"""
    from backend.app.schemas import StudentItemResultPatch
    from backend.app.services.exams import override_student_item_result
    student, question = seed["students"][0], seed["q1"]  # 满分 10，原始 3 分（错）
    row = override_student_item_result(
        db, seed["exam"].id, student.id, question.id,
        StudentItemResultPatch(score=10.0, override_note="复核后满分"),
        term_id=seed["term"].id)
    assert row["score"] == 10.0
    assert row["score_rate"] == pytest.approx(1.0)
    assert row["correct"] is True

    row = override_student_item_result(
        db, seed["exam"].id, student.id, question.id,
        StudentItemResultPatch(score=2.0, override_note="改判低分"),
        term_id=seed["term"].id)
    assert row["score_rate"] == pytest.approx(0.2)
    assert row["correct"] is False


def test_tool_policies():
    reads = tool_policy_for("general_chat", has_packet=False)
    assert tool_policy_for("exam_analysis", has_packet=True) == ["submit_report", *reads]
    assert tool_policy_for("exam_analysis", has_packet=True,
                           optional_tools=["get_teaching_guidance"]) == ["submit_report", "get_teaching_guidance", *reads]
    assert tool_policy_for("exam_analysis", has_packet=False) == ["submit_report", "get_formal_attachment"]


def test_packet_text_includes_evidence_ids(db, seed):
    """回归（评审 P1）：注入文本必须带 evidence ID——标准路径下模型唯一
    能看到的证据 ID 来源就是这份文本。"""
    packet = build_packet(db, "exam_analysis", _scope(seed))
    text = packet_to_text(packet)
    assert "证据:" in text
    referenced = {evid for d in packet["diagnostics"]
                  for evid in d["evidence_ids"]}
    assert referenced, "夹具应产生带证据的诊断"
    for evid in referenced:
        assert evid in text, f"诊断证据 {evid} 未出现在注入文本中"


def test_packet_text_budget_trims_whole_entries(db, seed, monkeypatch):
    """回归（评审 P2）：预算裁剪必须有效——逐条装入、整条丢弃、局限保留。"""
    from backend.app.agent import analysis_packet as ap
    monkeypatch.setattr(ap, "_PACKET_CHAR_BUDGET", 500)
    packet = build_packet(db, "exam_analysis", _scope(seed))
    packet["diagnostics"] = [
        {**d, "signal": d["signal"] + "补充说明" * 40,
         "actions": ["行动方案" * 20]}
        for d in packet["diagnostics"]
    ]
    text = packet_to_text(packet)
    lines = text.splitlines()
    numbered = [line for line in lines if line.split(".", 1)[0].isdigit()]
    assert not numbered, "单条诊断自身超出剩余预算时也必须整条丢弃"
    assert len(text) <= 500, f"最终注入文本不得突破预算：{len(text)}"
    assert any(line.startswith("局限：") for line in lines), "局限不得被裁掉"
    # 整条丢弃：编号必须连续（1..N），不得出现残句
    assert [line.split(".")[0] for line in numbered] == \
        [str(i) for i in range(1, len(numbered) + 1)]
