"""成长事实进入分析包与画像的回归测试（方案 §6 / §8 阶段 2）。

覆盖：

* 成长摘要由后端确定性输出，撤销后证据不再计入，缺证据不用 0 填充；
* 组装分析包时冻结成长事实指纹与证据 ID；
* 画像「依据已更新」的过期判断（记录修订号 → 事实更正 → 标记过期）；
* 模型不可用不影响成长事实本身（本模块不调用任何模型）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.agent.analysis_packet import build_packet, packet_to_text
from backend.app.database import Base
from backend.app.models import (
    AnalysisEvidence,
    AnalysisRun,
    Class,
    Enrollment,
    Exam,
    ExamPaperVersion,
    ExamQuestion,
    ExamScore,
    Student,
    StudentItemResult,
    StudentProfile,
    Term,
)
from backend.app.services.growth import events as growth_events
from backend.app.services.growth import rules as growth_rules
from backend.app.services.growth import summary as growth_summary


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/growth.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    term = Term(code="ZJ2026", name="2026 学年第一学期")
    session.add(term)
    session.flush()
    cls = Class(term_id=term.id, name="初三（1）班")
    session.add(cls)
    session.flush()
    strong = Student(student_no="2026001", name="张三", class_id=cls.id)
    quiet = Student(student_no="2026002", name="李四", class_id=cls.id)
    session.add_all([strong, quiet])
    session.flush()
    for student in (strong, quiet):
        session.add(Enrollment(term_id=term.id, class_id=cls.id,
                               student_id=student.id, status="active"))

    exam = Exam(term_id=term.id, name="期中考试", full_score=40,
                exam_date=date(2026, 4, 1), source_key="growth:mid")
    session.add(exam)
    session.flush()
    for student, total in ((strong, 34.0), (quiet, 18.0)):
        session.add(ExamScore(exam_id=exam.id, student_id=student.id,
                              total_score=total, class_id_at_exam=cls.id,
                              attendance_status="present"))
    paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed",
                             full_score=40)
    session.add(paper)
    session.flush()
    questions = []
    for no, q_type, max_score, nodes in (
        ("1", "完形填空", 10, ["固定搭配"]),
        ("2", "阅读理解", 10, ["细节定位"]),
        ("3", "语法填空", 10, ["动词时态"]),
        ("4", "书面表达", 10, ["任务完成"]),
    ):
        question = ExamQuestion(paper_version_id=paper.id, question_no=no,
                                question_type=q_type, max_score=max_score,
                                knowledge_nodes_json=nodes)
        session.add(question)
        questions.append(question)
    session.flush()
    item_scores = {strong.id: [3.0, 9.0, 8.0, 9.0], quiet.id: [2.0, 4.0, 5.0, 3.0]}
    for student_id, scores in item_scores.items():
        for question, score in zip(questions, scores):
            session.add(StudentItemResult(
                exam_id=exam.id, student_id=student_id,
                question_id=question.id, score=score,
                score_rate=round(score / question.max_score, 4),
                correct=score >= question.max_score,
                selected_option="A", attendance_status="present"))

    run = AnalysisRun(session_id=None, term_id=term.id,
                      capability="student_diagnosis", status="running",
                      class_id=cls.id, exam_id=exam.id, student_id=strong.id)
    session.add(run)
    session.commit()
    return {"session": session, "term": term, "cls": cls, "exam": exam,
            "strong": strong, "quiet": quiet, "run": run, "tmp": tmp_path}


def _add_activity(session, *, student_id, term_id, class_id, event_type="task_completed",
                  day="2026-04-02", note="完成课堂任务", key=None):
    return growth_events.record_event(
        session, student_id=student_id, term_id=term_id, class_id_at_event=class_id,
        event_type=event_type, occurred_at=datetime(2026, 4, 2, 9, 0, tzinfo=timezone.utc),
        business_date=date.fromisoformat(day), payload={"note": note},
        idempotency_key=key)


def _today_business_date() -> str:
    """按规则时区取当前业务日，避免「本周新增」断言依赖测试运行日期。"""
    return growth_rules.business_date_for(datetime.now(timezone.utc)).isoformat()


def test_summary_reports_deterministic_facts_and_evidence_refs(env):
    session, term, cls, strong = env["session"], env["term"], env["cls"], env["strong"]
    today = _today_business_date()
    first = _add_activity(session, student_id=strong.id, term_id=term.id,
                          class_id=cls.id, day=today, key="a1")
    _add_activity(session, student_id=strong.id, term_id=term.id, class_id=cls.id,
                  event_type="correction_verified", day=today, key="a2")
    session.commit()

    summary = growth_summary.build_growth_summary(
        session, student_id=strong.id, term_id=term.id)

    assert summary["schema_version"] == "1.0"
    assert summary["rule_version"] == growth_rules.DEFAULT_RULE_CODE
    assert summary["term_points"] == 4
    assert summary["week_points"] == 4
    assert summary["observed_task_completion"] == {
        "status": "no_task_ledger", "completed": 1, "eligible": None}
    assert summary["verified_corrections"] == 1
    assert summary["evidence_status"] == "recorded"
    assert f"growth-event:{first.id}" in summary["evidence_refs"]
    # 数值与事件明细一致：每一条证据引用都能在账本里找到。
    assert all(ref.startswith("growth-event:") for ref in summary["evidence_refs"])
    # 没有任务账本时明确说明，而不是编造分母。
    assert any("任务账本" in item for item in summary["limitations"])
    # 成长积分不等于英语能力：必须显式声明边界。
    assert any("不等于英语能力水平" in item for item in summary["limitations"])
    # 缺证据的维度用状态标注，不用 0 填充。
    for key, value in summary["dimensions"].items():
        assert "status" in value
        assert value["status"] in {"no_evidence", "insufficient_comparable_history", "ok"}


def test_summary_without_evidence_is_marked_not_zero_filled(env):
    session, term, quiet = env["session"], env["term"], env["quiet"]
    summary = growth_summary.build_growth_summary(
        session, student_id=quiet.id, term_id=term.id)

    assert summary["term_points"] == 0
    assert summary["evidence_status"] == "no_evidence"
    assert summary["evidence_refs"] == []
    assert summary["observed_task_completion"]["completed"] == 0
    # 无记录必须写成「暂无记录」，不能写成薄弱或 0 分表现。
    assert any("暂无记录" in item for item in summary["limitations"])


def test_reversed_events_drop_out_of_evidence(env):
    session, term, cls, strong = env["session"], env["term"], env["cls"], env["strong"]
    original = _add_activity(session, student_id=strong.id, term_id=term.id,
                             class_id=cls.id, key="rev-1")
    session.commit()
    assert growth_summary.build_growth_summary(
        session, student_id=strong.id, term_id=term.id)["term_points"] == 2

    growth_events.record_event(
        session, student_id=strong.id, term_id=term.id, class_id_at_event=cls.id,
        event_type="reversal", occurred_at=datetime(2026, 4, 3, 9, 0, tzinfo=timezone.utc),
        business_date=date(2026, 4, 3), payload={"note": "误录更正"},
        reverses_event_id=original.id, idempotency_key="rev-1-undo")
    session.commit()

    summary = growth_summary.build_growth_summary(
        session, student_id=strong.id, term_id=term.id)
    assert summary["term_points"] == 0
    assert summary["evidence_status"] == "no_evidence"
    assert summary["evidence_refs"] == []


def test_packet_freezes_growth_reference_and_evidence(env):
    session, term, cls, strong, run = (
        env["session"], env["term"], env["cls"], env["strong"], env["run"])
    _add_activity(session, student_id=strong.id, term_id=term.id,
                  class_id=cls.id, key="pkt-1")
    session.commit()

    scope = {"run_id": run.id, "term_id": term.id, "class_id": cls.id,
             "exam_id": env["exam"].id, "student_id": strong.id}
    packet = build_packet(session, "student_diagnosis", scope)
    session.commit()

    assert packet is not None
    growth = packet["growth_summary"]
    assert growth["term_points"] == 2
    assert growth["evidence_refs"]

    # 冻结在本次运行上，供报告落库后回写画像。
    session.refresh(run)
    frozen = (run.input_summary_json or {})["growth_reference"]
    assert frozen["revision"] == growth["source_revision"]
    assert frozen["rule_version"] == growth["rule_version"]
    assert frozen["evidence_refs"] == growth["evidence_refs"][:20]

    # 证据 ID 必须出现在注入文本中，模型才能引用。
    text = packet_to_text(packet)
    assert "成长事实 JSON" in text
    assert "不得改写" in text
    assert packet["growth_evidence_id"] in text
    assert growth["source_revision"] in text


def test_packet_marks_profile_stale_after_fact_correction(env):
    session, term, cls, strong, run = (
        env["session"], env["term"], env["cls"], env["strong"], env["run"])
    _add_activity(session, student_id=strong.id, term_id=term.id,
                  class_id=cls.id, key="stale-1")
    session.commit()

    scope = {"run_id": run.id, "term_id": term.id, "class_id": cls.id,
             "exam_id": env["exam"].id, "student_id": strong.id}
    packet = build_packet(session, "student_diagnosis", scope)
    session.commit()
    # 尚无画像：状态为 absent，不能声称已有结论。
    assert packet["growth_profile"]["status"] == "absent"

    # 模拟报告落库后回写画像引用（服务端行为，模型无法写入该字段）。
    profile = StudentProfile(student_id=strong.id, term_id=term.id,
                             profile_json={"summary": "该生学习活动较持续。"})
    session.add(profile)
    session.flush()
    growth_summary.record_growth_reference(
        session, student_id=strong.id, term_id=term.id,
        reference=(run.input_summary_json or {})["growth_reference"])
    session.commit()

    status = growth_summary.growth_profile_status(
        session, student_id=strong.id, term_id=term.id,
        current_revision=packet["growth_summary"]["source_revision"])
    assert status["status"] == "current"

    # 事实更正（新增一次活动）→ 画像标记「依据已更新」。
    _add_activity(session, student_id=strong.id, term_id=term.id, class_id=cls.id,
                  event_type="spaced_review", key="stale-2")
    session.commit()
    refreshed = build_packet(session, "student_diagnosis", scope)
    session.commit()
    assert refreshed["growth_profile"]["status"] == "stale"
    assert refreshed["growth_profile"]["label"] == "依据已更新"
    assert "依据已更新" in packet_to_text(refreshed)


def test_profile_growth_reference_is_not_model_writable(env):
    """模型补丁白名单不含成长引用字段，数值只能由后端写入。"""
    from backend.app.services.student_profiles import normalize_patch

    with pytest.raises(ValueError):
        normalize_patch({"growth_reference": {"revision": "forged"}})
    with pytest.raises(ValueError):
        normalize_patch({"growth_reference_json": {"revision": "forged"}})


def test_growth_evidence_is_recorded_in_ledger(env):
    """成长事实以独立证据类型入账，可追溯到学生与学期。"""
    session, term, cls, strong, run = (
        env["session"], env["term"], env["cls"], env["strong"], env["run"])
    scope = {"run_id": run.id, "term_id": term.id, "class_id": cls.id,
             "exam_id": env["exam"].id, "student_id": strong.id}
    build_packet(session, "student_diagnosis", scope)
    session.commit()

    rows = session.scalars(select(AnalysisEvidence).where(
        AnalysisEvidence.run_id == run.id,
        AnalysisEvidence.evidence_type == "growth_fact")).all()
    assert len(rows) == 1
    assert rows[0].source_entity == f"growth:{strong.id}:{term.id}"
