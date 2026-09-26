"""Regression coverage for corrected growth ledger and evidence boundaries."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    ExamPaperVersion, ExamQuestion, ExamScore, GrowthEvent,
    Class, GrowthTermRule, Student, StudentGrowthSnapshot, StudentItemResult, Term,
)
from backend.app.services.growth import events, exam_events, rules, service, snapshots
from backend.app.database import _alembic_config
from backend.tests.test_growth_three_part import _add_exam, env as basic_env
from backend.tests.test_growth_profile_integration import env as profile_env


def _activity(identifier, kind="task_completed", day=date(2026, 4, 1), target=None):
    return {
        "id": identifier, "event_type": kind, "business_date": day,
        "occurred_at": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        + timedelta(hours=identifier), "reverses_event_id": target,
    }


def test_reversal_reallocates_caps_and_breaks_streak():
    rule = rules.build_default_rule_version()
    awards = rules.compute_awards([
        _activity(1), _activity(2), _activity(3),
        _activity(4, "reversal", target=1),
    ], rule=rule)
    assert [item["applied_points"] for item in awards] == [0, 2, 2, 0]
    assert rules.term_points_from_awards(awards) == 4

    start = date(2026, 4, 1)
    weeks = rules.compute_awards([
        _activity(1, "weekly_goal", start),
        _activity(2, "weekly_goal", start + timedelta(days=7)),
        _activity(3, "weekly_goal", start + timedelta(days=14)),
        _activity(4, "reversal", target=1),
    ], rule=rule)
    assert [item["applied_points"] for item in weeks] == [0, 2, 2, 0]


def test_score_correction_reconciles_and_can_resync_after_reversal(tmp_path):
    setup = basic_env.__wrapped__(tmp_path)
    session, term, cls, student = (setup[key] for key in
                                   ("session", "term", "cls", "strong"))
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-01",
              key="first", scores={student.id: 60})
    later = _add_exam(session, term_id=term.id, class_id=cls.id,
                      day="2026-04-08", key="later", scores={student.id: 70})
    exam_events.sync_exam_growth_events(session, term_id=term.id)
    score = session.scalar(select(ExamScore).where(ExamScore.exam_id == later.id))
    score.total_score = 60
    session.flush()
    result = exam_events.sync_exam_growth_events(session, term_id=term.id)
    assert result["created"] == 1
    assert service.student_detail(session, term_id=term.id,
                                  student_id=student.id)["snapshot"]["term_points"] == 4
    current = session.scalar(select(GrowthEvent).where(
        GrowthEvent.source_type == "auto_exam",
        GrowthEvent.source_id == f"{later.id}:{student.id}",
    ).order_by(GrowthEvent.id.desc()))
    assert current.payload_json["progress_points"] == 0
    service.reverse_event(session, term_id=term.id, event_id=current.id,
                          reason="确认误录")
    assert exam_events.sync_exam_growth_events(session, term_id=term.id)["created"] == 0
    score.total_score = 80
    session.flush()
    assert exam_events.sync_exam_growth_events(session, term_id=term.id)["created"] == 1
    assert service.student_detail(session, term_id=term.id,
                                  student_id=student.id)["snapshot"]["term_points"] == 6
    score.total_score = 60
    session.flush()
    assert exam_events.sync_exam_growth_events(session, term_id=term.id)["created"] == 1
    assert service.student_detail(session, term_id=term.id,
                                  student_id=student.id)["snapshot"]["term_points"] == 4


def test_same_rebound_is_not_awarded_twice(tmp_path):
    setup = basic_env.__wrapped__(tmp_path)
    session, term, cls, student = (setup[key] for key in
                                   ("session", "term", "cls", "strong"))
    for day, score in zip((1, 8, 15, 22), (60, 70, 60, 70)):
        _add_exam(session, term_id=term.id, class_id=cls.id,
                  day=f"2026-04-{day:02d}", key=f"week-{day}",
                  scores={student.id: score})
    exam_events.sync_exam_growth_events(session, term_id=term.id)
    progress = [item.payload_json["progress_points"] for item in session.scalars(
        select(GrowthEvent).where(
            GrowthEvent.student_id == student.id,
            GrowthEvent.event_type == "exam_completed",
        ).order_by(GrowthEvent.business_date))]
    assert progress == [0, 2, 0, 0]


@pytest.mark.parametrize("restore_kind", ["attendance", "archive"])
def test_system_reversal_restores_growth_event_with_source(restore_kind, tmp_path):
    case_dir = tmp_path / restore_kind
    case_dir.mkdir()
    setup = basic_env.__wrapped__(case_dir)
    session, term, cls, student = (setup[key] for key in
                                   ("session", "term", "cls", "strong"))
    exam = _add_exam(session, term_id=term.id, class_id=cls.id,
                     day="2026-04-01", key=restore_kind,
                     scores={student.id: 60})
    initial = exam_events.sync_exam_growth_events(session, term_id=term.id)
    assert initial["created"] == 1
    assert service.student_detail(session, term_id=term.id,
                                  student_id=student.id)["snapshot"]["term_points"] == 2

    score = session.scalar(select(ExamScore).where(ExamScore.exam_id == exam.id))
    if restore_kind == "attendance":
        score.attendance_status = "absent"
    else:
        exam.status = "archived"
    exam_events.sync_exam_growth_events(session, term_id=term.id)
    assert service.student_detail(session, term_id=term.id,
                                  student_id=student.id)["snapshot"]["term_points"] == 0

    if restore_kind == "attendance":
        score.attendance_status = "present"
    else:
        exam.status = "active"
    restored = exam_events.sync_exam_growth_events(session, term_id=term.id)
    assert restored["created"] == 1
    assert service.student_detail(session, term_id=term.id,
                                  student_id=student.id)["snapshot"]["term_points"] == 2
    assert exam_events.sync_exam_growth_events(session, term_id=term.id)["created"] == 0

    current = session.scalar(select(GrowthEvent).where(
        GrowthEvent.source_type == "auto_exam",
        GrowthEvent.source_id == f"{exam.id}:{student.id}",
    ).order_by(GrowthEvent.id.desc()))
    service.reverse_event(session, term_id=term.id, event_id=current.id,
                          reason="教师确认误录")
    assert exam_events.sync_exam_growth_events(session, term_id=term.id)["created"] == 0
    assert service.student_detail(session, term_id=term.id,
                                  student_id=student.id)["snapshot"]["term_points"] == 0


def test_draft_is_excluded_and_confirmed_score_changes_revision(tmp_path):
    setup = profile_env.__wrapped__(tmp_path)
    session, student, term, exam = (setup[key] for key in
                                    ("session", "strong", "term", "exam"))
    before = snapshots.build_snapshot(session, student_id=student.id,
                                      term_id=term.id, persist=False)
    draft = ExamPaperVersion(exam_id=exam.id, version=2, status="draft", full_score=40)
    session.add(draft)
    session.flush()
    question = ExamQuestion(paper_version_id=draft.id, question_no="draft",
                            question_type="阅读理解", max_score=10)
    session.add(question)
    session.flush()
    session.add(StudentItemResult(exam_id=exam.id, student_id=student.id,
                                  question_id=question.id, score=0,
                                  attendance_status="present"))
    session.flush()
    after_draft = snapshots.build_snapshot(session, student_id=student.id,
                                           term_id=term.id, persist=False)
    assert after_draft["dimensions"]["reading"]["value"] == before["dimensions"]["reading"]["value"]
    assert after_draft["source_revision"] == before["source_revision"]

    confirmed = session.scalar(select(StudentItemResult).join(
        ExamQuestion, StudentItemResult.question_id == ExamQuestion.id).where(
        StudentItemResult.student_id == student.id,
        ExamQuestion.question_type == "阅读理解",
        ExamQuestion.paper_version_id != draft.id))
    confirmed.score = 0
    session.flush()
    after_score = snapshots.build_snapshot(session, student_id=student.id,
                                           term_id=term.id, persist=False)
    assert after_score["dimensions"]["reading"]["value"] < before["dimensions"]["reading"]["value"]
    assert after_score["source_revision"] != before["source_revision"]


def test_forest_get_does_not_write_awards_or_snapshots(tmp_path):
    setup = basic_env.__wrapped__(tmp_path)
    session, term = setup["session"], setup["term"]
    writes = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(session.bind, "before_cursor_execute", capture)
    try:
        result = service.forest_rows(session, term_id=term.id)
    finally:
        event.remove(session.bind, "before_cursor_execute", capture)
    assert result["summary"]["student_count"] == 2
    assert writes == []


def test_existing_term_keeps_its_rule_version(tmp_path):
    setup = basic_env.__wrapped__(tmp_path)
    session, term, student = (setup[key] for key in
                              ("session", "term", "strong"))
    old_rule = rules.build_default_rule_version()
    old_rule.code = "growth-v3"
    session.add(old_rule)
    session.flush()
    snapshots.build_snapshot(session, student_id=student.id,
                             term_id=term.id, rule=old_rule)
    assert events.read_rule_version(session, term_id=term.id).code == "growth-v3"
    assert events.ensure_rule_version(session, term_id=term.id).code == "growth-v3"


def test_manual_bonus_repeats_only_for_distinct_confirmed_requests(tmp_path):
    setup = basic_env.__wrapped__(tmp_path)
    session, term, student = (setup[key] for key in
                              ("session", "term", "strong"))
    first = service.record_batch(session, term_id=term.id,
                                 request_id="teacher-bonus-1", items=[{
        "student_id": student.id, "event_type": "teacher_bonus",
        "note": "主动帮助同学订正", "points": 3,
    }])
    assert len(first["created"]) == 1
    assert session.get(GrowthTermRule, term.id).rule_code == "growth-v4"
    assert service.forest_rows(session, term_id=term.id)["students"][0]["term_points"] == 3
    repeat = service.record_batch(session, term_id=term.id,
                                  request_id="teacher-bonus-1", items=[{
        "student_id": student.id, "event_type": "teacher_bonus",
        "note": "主动帮助同学订正", "points": 3,
    }])
    assert len(repeat["created"]) == 0
    service.record_batch(session, term_id=term.id,
                         request_id="teacher-bonus-2", items=[{
        "student_id": student.id, "event_type": "teacher_bonus",
        "note": "独立完成课堂展示", "points": 3,
    }])
    assert service.forest_rows(session, term_id=term.id)["students"][0]["term_points"] == 6


def test_rule_binding_migration_preserves_old_term(tmp_path):
    database_url = f"sqlite:///{tmp_path}/old-growth.db"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260926_0029")
    engine = create_engine(database_url)
    with sessionmaker(bind=engine)() as session:
        term = Term(code="old", name="旧学期")
        session.add(term)
        session.flush()
        classroom = Class(term_id=term.id, name="一班")
        session.add(classroom)
        session.flush()
        student = Student(student_no="01", name="甲", class_id=classroom.id)
        session.add(student)
        session.flush()
        old = rules.build_default_rule_version()
        old.code = "growth-v3"
        session.add(old)
        session.add(StudentGrowthSnapshot(student_id=student.id,
                                          term_id=term.id, rule_version=old.code))
        legacy_term_id = term.id
        session.commit()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        bound = connection.execute(text(
            "SELECT rule_code FROM growth_term_rules WHERE term_id = :term_id"),
            {"term_id": legacy_term_id}).scalar_one()
    assert bound == "growth-v3"


def test_new_manual_policy_preserves_historical_caps_and_automatic_allowance(tmp_path):
    setup = basic_env.__wrapped__(tmp_path)
    session, term, student = (setup[key] for key in ("session", "term", "strong"))
    when = datetime(2026, 4, 1, 8, tzinfo=timezone.utc)
    old_ids = []
    for i in range(3):
        old = events.record_event(session, student_id=student.id, term_id=term.id,
                                  event_type="task_completed", occurred_at=when,
                                  payload={"note": "历史补录"}, idempotency_key=f"old-{i}")
        old_ids.append(old.id)
    created = service.record_batch(session, term_id=term.id, request_id="new-policy", items=[{
        "student_id": student.id, "event_type": "weekly_goal", "points": 50,
        "note": "教师确认", "occurred_at": when.isoformat(),
    }])
    automatic = events.record_event(session, student_id=student.id, term_id=term.id,
                                    event_type="exam_completed", occurred_at=when,
                                    source_type="auto_exam", idempotency_key="auto-after-manual")
    awards = {a.event_id: a for a in events.calculate_awards(
        session, student_id=student.id, term_id=term.id)}
    assert [awards[i].applied_points for i in old_ids] == [2, 2, 0]
    assert awards[old_ids[-1]].cap_reason == "category_daily_awards"
    assert awards[automatic.id].applied_points == 2
    new_id = created["created"][0]["event_id"]
    assert awards[new_id].applied_points == 50
    service.reverse_event(session, term_id=term.id, event_id=new_id, reason="误录")
    awards = {a.event_id: a for a in events.calculate_awards(
        session, student_id=student.id, term_id=term.id)}
    assert awards[new_id].applied_points == 0
    assert [awards[i].applied_points for i in old_ids] == [2, 2, 0]
    assert awards[automatic.id].applied_points == 2
