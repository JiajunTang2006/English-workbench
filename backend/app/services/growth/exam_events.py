"""Reconcile immutable exam growth events with the current score facts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy import and_, or_, select

from ...models import Enrollment, Exam, ExamScore, GrowthEvent
from ...models.entities import utcnow
from . import events as growth_events
from . import rules, snapshots


def score_rate_for(total_score: Any, full_score: Any) -> float | None:
    """有效得分率；总分缺失/为负或满分非正时返回 ``None``（缺失不补零）。"""
    try:
        total = float(total_score)
        full = float(full_score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(full) or not math.isfinite(total) or full <= 0 or total < 0:
        return None
    return max(0.0, min(1.0, total / full))


def _previous_rate(session, *, student_id: int, term_id: int, exam: Exam,
                   rule_code: str) -> float | None:
    """Recent comparable-series high-water mark, to avoid rewarding a rebound twice."""
    if exam.exam_date is not None:
        earlier = or_(
            Exam.exam_date < exam.exam_date,
            and_(Exam.exam_date == exam.exam_date, Exam.id < exam.id),
        )
    else:
        earlier = Exam.id < exam.id
    is_v4 = rule_code != "growth-v3"
    row = session.execute(
        select(ExamScore.total_score, Exam.full_score)
        .join(Exam, Exam.id == ExamScore.exam_id)
        .where(
            ExamScore.student_id == student_id,
            Exam.term_id == term_id,
            Exam.status == "active",
            *((Exam.exam_type == exam.exam_type,
               Exam.exam_kind == exam.exam_kind,
               Exam.full_score == exam.full_score) if is_v4 else ()),
            ExamScore.attendance_status == "present",
            ExamScore.total_score.is_not(None),
            Exam.full_score > 0,
            earlier,
        )
        .order_by(Exam.exam_date.desc(), Exam.id.desc())
        .limit(3 if is_v4 else 1)
    ).all()
    rates = [rate for total, full in row
             if (rate := score_rate_for(total, full)) is not None]
    if not rates:
        return None
    return max(rates)


def _occurred_at(exam: Exam) -> datetime:
    day = exam.exam_date or utcnow().date()
    return datetime.combine(day, time(0, 0), tzinfo=timezone.utc)


def sync_exam_growth_events(session, *, term_id: int) -> dict[str, Any]:
    """幂等地把本学期已生效考试的参加与进步记入成长账本。

    返回 ``{"created", "skipped", "students"}``。规则版本没有 ``exam_completed``
    定义时（例如仍在按旧规则复算）直接跳过，不做任何写入。
    """
    rule = growth_events.ensure_rule_version(session, term_id=term_id)
    rule_defs = rule.event_rules_json or {}
    exam_rule = rule_defs.get("exam_completed")
    if not isinstance(exam_rule, dict):
        return {"created": 0, "skipped": 0, "students": []}

    progress_cfg = exam_rule.get("progress") or {}
    step = progress_cfg.get("step") or 0.05
    cap = int(progress_cfg.get("max") or 0)

    exams = list(session.scalars(
        select(Exam)
        .where(Exam.term_id == term_id, Exam.status == "active")
        .order_by(Exam.exam_date, Exam.id)
    ))
    event_rows = list(session.scalars(
        select(GrowthEvent).where(
            GrowthEvent.term_id == term_id,
            GrowthEvent.source_type == "auto_exam",
        ).order_by(GrowthEvent.id)
    ))
    reversal_events = {
        event.reverses_event_id: event
        for event in session.scalars(
            select(GrowthEvent)
            .where(
                GrowthEvent.term_id == term_id,
                GrowthEvent.reverses_event_id.is_not(None),
            )
            .order_by(GrowthEvent.id)
        )
    }
    reversals = set(reversal_events)
    by_source: dict[str, list[GrowthEvent]] = {}
    for event in event_rows:
        by_source.setdefault(event.source_id or "", []).append(event)
    created: list[int] = []
    changed: set[int] = set()
    seen_sources: set[str] = set()
    skipped = 0
    for exam in exams:
        rows = session.execute(
            select(ExamScore, Enrollment)
            .join(Enrollment, and_(
                Enrollment.student_id == ExamScore.student_id,
                Enrollment.term_id == term_id,
                Enrollment.status == "active",
            ))
            .where(ExamScore.exam_id == exam.id)
            .order_by(ExamScore.student_id)
        ).all()
        for score, enrollment in rows:
            source_id = f"{exam.id}:{score.student_id}"
            seen_sources.add(source_id)
            previous = by_source.get(source_id, [])
            active = next((event for event in reversed(previous)
                           if event.id not in reversals), None)
            baseline = _previous_rate(
                session, student_id=score.student_id, term_id=term_id,
                exam=exam, rule_code=rule.code)
            rate = score_rate_for(score.total_score, exam.full_score)
            if score.attendance_status != "present":
                skipped += 1
                rate = None
            elif rate is None:
                skipped += 1
            progress = rules.compute_progress_bonus(
                score_rate=rate, baseline_rate=baseline, step=step, cap=cap)
            revision_data = [exam.exam_date.isoformat() if exam.exam_date else None,
                             exam.exam_type, exam.exam_kind, exam.full_score,
                             score.attendance_status, score.total_score,
                             baseline, progress]
            revision = hashlib.sha256(json.dumps(
                revision_data, separators=(",", ":")).encode()).hexdigest()[:32]
            # An explicit teacher reversal of an unchanged source stays revoked.
            last = previous[-1] if previous else None
            if last and last.source_revision is None and rate is not None:
                old = last.payload_json or {}
                reversal = reversal_events.get(last.id)
                if (old.get("score_rate") == round(rate, 4)
                        and old.get("baseline_rate") == (
                            None if baseline is None else round(baseline, 4))
                        and old.get("progress_points") == progress
                        and (reversal is None or reversal.actor != "system")):
                    skipped += 1
                    continue
            if last and last.source_revision == revision:
                reversal = reversal_events.get(last.id)
                if reversal is None or reversal.actor != "system":
                    skipped += 1
                    continue
            if active is not None:
                growth_events.record_event(
                    session, student_id=score.student_id, term_id=term_id,
                    event_type="reversal", occurred_at=utcnow(),
                    source_type="correction", source_id=str(active.id),
                    payload={"note": "考试成绩来源更新"}, actor="system",
                    reverses_event_id=active.id,
                    idempotency_key=growth_events.make_idempotency_key(
                        "growth-exam-correction", active.id))
                reversals.add(active.id)
                changed.add(score.student_id)
            if rate is None:
                continue
            key = growth_events.make_idempotency_key(
                "growth-exam", exam.id, score.student_id, revision,
                len(previous))
            growth_events.record_event(
                session,
                student_id=score.student_id,
                term_id=term_id,
                event_type="exam_completed",
                occurred_at=_occurred_at(exam),
                business_date=exam.exam_date or rules.business_date_for(
                    utcnow(), rule.timezone),
                class_id_at_event=enrollment.class_id,
                source_type="auto_exam",
                source_id=source_id,
                source_revision=revision,
                payload={
                    "exam_id": exam.id,
                    "exam_name": exam.name,
                    "score_rate": round(rate, 4),
                    "baseline_rate": None if baseline is None else round(baseline, 4),
                    "progress_points": progress,
                    "note": f"参加{exam.name}",
                },
                actor="system",
                idempotency_key=key,
            )
            created.append(score.student_id)
            changed.add(score.student_id)

    # An archived/deleted exam or removed score must no longer contribute.
    for source_id, previous in by_source.items():
        if source_id in seen_sources:
            continue
        active = next((item for item in reversed(previous)
                       if item.id not in reversals), None)
        if active is None:
            continue
        growth_events.record_event(
            session, student_id=active.student_id, term_id=term_id,
            event_type="reversal", occurred_at=utcnow(),
            source_type="correction", source_id=str(active.id),
            payload={"note": "考试或成绩已移除"}, actor="system",
            reverses_event_id=active.id,
            idempotency_key=growth_events.make_idempotency_key(
                "growth-exam-correction", active.id))
        changed.add(active.student_id)

    for student_id in sorted(changed):
        snapshots.build_snapshot(session, student_id=student_id, term_id=term_id, rule=rule)
    return {"created": len(created), "skipped": skipped, "students": sorted(changed)}
