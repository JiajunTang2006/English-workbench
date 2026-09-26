"""三段式计分回归：个人进步与持续投入（方案 §3.1 / §4.1）。

覆盖：

* 进步分相对学生自己的近期同类测评基线；相同百分点提升同奖；
* 缺失基线 / 退步 / 无提升一律 0 分，不补零、不扣分；
* 里程碑分随连续达成周数递增并封顶，断周清零；
* 考试自动事件：参加即得基础分、相对上次提升得进步分；缺考/空分不生成；
* 幂等：重复同步同一份成绩不重复计分；
* 撤销后进步分随之回落；超满分截断为 1.0。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models import (
    Class,
    Enrollment,
    Exam,
    ExamScore,
    GrowthEvent,
    Student,
    Term,
)
from backend.app.services.growth import events as growth_events
from backend.app.services.growth import exam_events
from backend.app.services.growth import rules as growth_rules
from backend.app.services.growth import service as growth_service


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/growth3.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    term = Term(code="ZJ2026", name="2026 学年第一学期")
    session.add(term)
    session.flush()
    cls = Class(term_id=term.id, name="初三（1）班")
    session.add(cls)
    session.flush()
    strong = Student(student_no="2026001", name="张强", class_id=cls.id)
    weak = Student(student_no="2026002", name="李弱", class_id=cls.id)
    session.add_all([strong, weak])
    session.flush()
    for student in (strong, weak):
        session.add(Enrollment(term_id=term.id, class_id=cls.id,
                               student_id=student.id, status="active"))
    session.commit()
    return {"session": session, "term": term, "cls": cls,
            "strong": strong, "weak": weak}


def _add_exam(session, *, term_id, class_id, day, key, scores,
              full_score=100, attendance=None):
    """建一场考试并写入若干学生的总分；``scores`` 形如 {student_id: total}。"""
    exam = Exam(term_id=term_id, name=f"单元测{key}", full_score=full_score,
                exam_date=date.fromisoformat(day), source_key=key, status="active")
    session.add(exam)
    session.flush()
    for student_id, total in scores.items():
        session.add(ExamScore(
            exam_id=exam.id, student_id=student_id, total_score=total,
            class_id_at_exam=class_id,
            attendance_status=(attendance or {}).get(student_id, "present")))
    session.flush()
    return exam


# ------------------------------------------------------------------ 进步分（纯函数）


def test_progress_bonus_is_relative_to_own_baseline():
    """相同百分点提升同奖；高分天花板只限制可实现的提升幅度。"""
    step, cap = 0.05, 2
    weak = growth_rules.compute_progress_bonus(
        score_rate=0.45, baseline_rate=0.35, step=step, cap=cap)
    strong = growth_rules.compute_progress_bonus(
        score_rate=0.85, baseline_rate=0.75, step=step, cap=cap)
    assert weak == 2
    assert strong == 2


def test_progress_bonus_missing_or_regression_is_zero():
    assert growth_rules.compute_progress_bonus(
        score_rate=0.6, baseline_rate=None, step=0.05, cap=2) == 0
    assert growth_rules.compute_progress_bonus(
        score_rate=None, baseline_rate=0.4, step=0.05, cap=2) == 0
    assert growth_rules.compute_progress_bonus(
        score_rate=0.5, baseline_rate=0.5, step=0.05, cap=2) == 0
    # 退步不扣分，只记 0
    assert growth_rules.compute_progress_bonus(
        score_rate=0.30, baseline_rate=0.55, step=0.05, cap=2) == 0


def test_progress_bonus_step_boundaries_survive_float_math():
    assert growth_rules.compute_progress_bonus(
        score_rate=0.10, baseline_rate=0.00, step=0.05, cap=3) == 2
    # 0.15 / 0.05 在浮点下是 2.9999…，必须仍算 3 分
    assert growth_rules.compute_progress_bonus(
        score_rate=0.15, baseline_rate=0.00, step=0.05, cap=3) == 3
    # 差一点点不到一档，不能进位
    assert growth_rules.compute_progress_bonus(
        score_rate=0.049, baseline_rate=0.00, step=0.05, cap=3) == 0
    # 封顶
    assert growth_rules.compute_progress_bonus(
        score_rate=0.90, baseline_rate=0.00, step=0.05, cap=2) == 2


# ------------------------------------------------------------------ 里程碑分（纯函数）


def test_milestone_bonus_grows_with_consecutive_weeks():
    def bonus(weeks):
        return growth_rules.compute_milestone_bonus(
            consecutive_weeks=weeks, every=2, cap=3)

    assert [bonus(w) for w in (1, 2, 3, 4, 5, 6, 7, 20)] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert growth_rules.compute_milestone_bonus(
        consecutive_weeks=None, every=2, cap=3) == 0


# ------------------------------------------------------------------ 封顶与阶段阈值


def test_caps_and_stages_are_recalibrated_for_three_part_scoring():
    assert growth_rules.DAILY_TOTAL_CAP == 10
    assert growth_rules.WEEKLY_TOTAL_CAP == 40
    thresholds = [int(stage["min"]) for stage in growth_rules.DEFAULT_STAGES]
    assert thresholds == [0, 30, 80, 160, 270, 400, 560]
    # 阶段名与图标沿用七种视觉
    assert [stage["name"] for stage in growth_rules.DEFAULT_STAGES] == [
        "种子", "发芽", "小树苗", "茁壮成长", "开花", "结果", "森林之星"]


# ------------------------------------------------------------------ 里程碑（compute_awards）


def _goal(day: str, key: str) -> dict:
    return {
        "id": abs(hash(key)) % 100000,
        "event_type": "weekly_goal",
        "occurred_at": datetime.fromisoformat(f"{day}T09:00:00+00:00"),
        "business_date": date.fromisoformat(day),
        "proposed_points": None,
        "progress_points": None,
        "reverses_event_id": None,
    }


def test_weekly_goal_milestone_rewards_consistency():
    rule = growth_rules.build_default_rule_version()
    events = [_goal(day, day) for day in
              ("2026-04-01", "2026-04-08", "2026-04-15", "2026-04-22", "2026-04-29")]
    awards = growth_rules.compute_awards(events, rule=rule)
    assert [item["milestone_points"] for item in awards] == [0, 0, 1, 1, 2]
    assert [item["proposed_points"] for item in awards] == [2, 2, 3, 3, 4]
    assert [item["applied_points"] for item in awards] == [2, 2, 3, 3, 4]


def test_weekly_goal_milestone_resets_after_a_gap():
    rule = growth_rules.build_default_rule_version()
    # 中间空了一周（W15），第二段重新从 0 开始
    events = [_goal(day, day) for day in ("2026-04-01", "2026-04-15", "2026-04-22")]
    awards = growth_rules.compute_awards(events, rule=rule)
    assert [item["milestone_points"] for item in awards] == [0, 0, 0]
    assert [item["proposed_points"] for item in awards] == [2, 2, 2]


# ------------------------------------------------------------------ 考试自动事件


def test_exam_participation_awards_base_and_relative_progress(env):
    session, term, cls = env["session"], env["term"], env["cls"]
    strong, weak = env["strong"], env["weak"]
    # 第一次：弱生 35 分，强生 90 分（建立基线）
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-01",
              key="e1", scores={weak.id: 35.0, strong.id: 90.0})
    # 第二次：弱生 45 分（+10pp → 进步 2），强生 95 分（+5pp → 进步 1）
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-08",
              key="e2", scores={weak.id: 45.0, strong.id: 95.0})
    session.commit()

    result = exam_events.sync_exam_growth_events(session, term_id=term.id)
    session.commit()
    assert result["created"] == 4

    weak_events = [event for event in growth_events.list_events(
        session, student_id=weak.id, term_id=term.id)
        if event.event_type == "exam_completed"]
    assert len(weak_events) == 2
    latest_weak = max(weak_events, key=lambda item: item.business_date)
    assert latest_weak.payload_json["progress_points"] == 2
    assert latest_weak.payload_json["baseline_rate"] == pytest.approx(0.35, abs=1e-6)

    strong_events = [event for event in growth_events.list_events(
        session, student_id=strong.id, term_id=term.id)
        if event.event_type == "exam_completed"]
    latest_strong = max(strong_events, key=lambda item: item.business_date)
    assert latest_strong.payload_json["progress_points"] == 1

    # 弱生第二次考试拿到 基础 2 + 进步 2 = 4；强生 基础 2 + 进步 1 = 3
    weak_snapshot = growth_service.student_detail(
        session, term_id=term.id, student_id=weak.id)["snapshot"]
    strong_snapshot = growth_service.student_detail(
        session, term_id=term.id, student_id=strong.id)["snapshot"]
    assert weak_snapshot["term_points"] == 2 + 4
    assert strong_snapshot["term_points"] == 2 + 3


def test_exam_sync_is_idempotent(env):
    session, term, cls = env["session"], env["term"], env["cls"]
    weak = env["weak"]
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-01",
              key="e1", scores={weak.id: 35.0})
    session.commit()

    first = exam_events.sync_exam_growth_events(session, term_id=term.id)
    session.commit()
    second = exam_events.sync_exam_growth_events(session, term_id=term.id)
    session.commit()
    assert first["created"] == 1
    assert second["created"] == 0
    count = session.scalar(select(func.count()).select_from(GrowthEvent).where(
        GrowthEvent.student_id == weak.id, GrowthEvent.event_type == "exam_completed"))
    assert count == 1


def test_absent_and_blank_scores_do_not_create_events(env):
    session, term, cls = env["session"], env["term"], env["cls"]
    weak, strong = env["weak"], env["strong"]
    # 弱生缺考、强生空分：都不生成事件
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-01", key="e1",
              scores={weak.id: 35.0, strong.id: None},
              attendance={weak.id: "absent"})
    session.commit()

    result = exam_events.sync_exam_growth_events(session, term_id=term.id)
    session.commit()
    assert result["created"] == 0
    assert session.scalar(select(func.count()).select_from(GrowthEvent).where(
        GrowthEvent.event_type == "exam_completed")) == 0


def test_zero_score_is_recorded_without_progress(env):
    """有效 0 分是真实结果：照常记基础分，但进步分为 0。"""
    session, term, cls = env["session"], env["term"], env["cls"]
    weak = env["weak"]
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-01",
              key="e1", scores={weak.id: 0.0})
    session.commit()

    exam_events.sync_exam_growth_events(session, term_id=term.id)
    session.commit()
    events = [event for event in growth_events.list_events(
        session, student_id=weak.id, term_id=term.id)
        if event.event_type == "exam_completed"]
    assert len(events) == 1
    assert events[0].payload_json["progress_points"] == 0


def test_over_full_score_is_clamped(env):
    """超满分按 1.0 截断，避免高起点自动获得数倍营养。"""
    session, term, cls = env["session"], env["term"], env["cls"]
    weak = env["weak"]
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-01",
              key="e1", scores={weak.id: 130.0}, full_score=100)
    session.commit()

    exam_events.sync_exam_growth_events(session, term_id=term.id)
    session.commit()
    event = [item for item in growth_events.list_events(
        session, student_id=weak.id, term_id=term.id)
        if item.event_type == "exam_completed"][0]
    assert event.payload_json["score_rate"] == 1.0


def test_reversal_drops_progress_points(env):
    session, term, cls = env["session"], env["term"], env["cls"]
    weak = env["weak"]
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-01",
              key="e1", scores={weak.id: 35.0})
    _add_exam(session, term_id=term.id, class_id=cls.id, day="2026-04-08",
              key="e2", scores={weak.id: 45.0})
    session.commit()
    exam_events.sync_exam_growth_events(session, term_id=term.id)
    session.commit()

    before = growth_service.student_detail(
        session, term_id=term.id, student_id=weak.id)["snapshot"]["term_points"]
    assert before == 2 + 4

    latest = max((event for event in growth_events.list_events(
        session, student_id=weak.id, term_id=term.id)
        if event.event_type == "exam_completed"), key=lambda item: item.business_date)
    growth_service.reverse_event(
        session, term_id=term.id, event_id=latest.id, reason="成绩录入错误")
    session.commit()

    after = growth_service.student_detail(
        session, term_id=term.id, student_id=weak.id)["snapshot"]["term_points"]
    assert after == 2  # 只剩第一次考试的基础分
