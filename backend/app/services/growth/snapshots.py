"""学期成长快照：可重建缓存，不是第二套事实。

所有数值都能由事件 + 规则重算；快照只是缓存。数值字段由后端确定性输出，
模型只能读取，不能修改积分或置信状态。
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import select

from ...models import GrowthEvent, StudentGrowthSnapshot
from ...models.entities import utcnow
from . import events as growth_events
from . import performance, rules


def build_snapshot(session, *, student_id: int, term_id: int,
                   rule=None, persist: bool = True) -> dict[str, Any]:
    """重算并（可选）持久化学生本学期成长快照。"""
    rule = rule or (growth_events.ensure_rule_version(session, term_id=term_id) if persist
                    else growth_events.read_rule_version(session, term_id=term_id))
    stages = rule.stage_thresholds_json or rules.DEFAULT_STAGES

    awards = (growth_events.rebuild_awards if persist else growth_events.calculate_awards)(
        session, student_id=student_id, term_id=term_id, rule=rule)
    event_rows = growth_events.list_events(
        session, student_id=student_id, term_id=term_id)

    term_points = rules.term_points_from_awards([
        {"applied_points": award.applied_points, "cap_reason": award.cap_reason}
        for award in awards
    ])
    # 历史营养（旧规则）只统计未被撤销的 legacy_manual 事件；整批撤销后归零，
    # 但原记录仍保留在事件账本中作为审计依据。
    reversed_ids = {event.reverses_event_id for event in event_rows
                    if event.reverses_event_id is not None}
    legacy_points = sum(
        int((event.payload_json or {}).get("legacy_points") or 0)
        for event in event_rows
        if event.event_type == "legacy_manual" and event.id not in reversed_ids
    )

    today = rules.business_date_for(datetime.now(timezone.utc), rule.timezone)
    current_week = rules.week_key_for(today)
    week_points = max(0, sum(
        award.applied_points for award in awards
        if award.week_key == current_week and award.cap_reason != "legacy"
    ))

    stage_index = rules.stage_index_for(term_points, stages)
    stage = stages[stage_index]

    business_dates = sorted({event.business_date for event in event_rows})
    by_type: dict[str, int] = {}
    for event in event_rows:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
    coverage = {
        "recorded_events": len(event_rows),
        "active_days": len(business_dates),
        "first_record_date": business_dates[0].isoformat() if business_dates else None,
        "last_record_date": business_dates[-1].isoformat() if business_dates else None,
        "by_type": by_type,
    }

    dimensions = performance.build_dimension_performance(
        session, student_id=student_id, term_id=term_id)
    source_revision = hashlib.sha256(json.dumps({
        "ledger": growth_events.events_source_revision(event_rows, awards),
        "dimensions": dimensions,
        "rule": rule.code,
        "stage_thresholds": stages,
        "week": current_week,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]

    snapshot = None
    if persist:
        snapshot = session.scalar(select(StudentGrowthSnapshot).where(
            StudentGrowthSnapshot.student_id == student_id,
            StudentGrowthSnapshot.term_id == term_id,
        ))
        if snapshot is None:
            snapshot = StudentGrowthSnapshot(student_id=student_id, term_id=term_id)
            session.add(snapshot)
        snapshot.rule_version = rule.code
        snapshot.term_points = term_points
        snapshot.legacy_points = legacy_points
        snapshot.stage_index = stage_index
        snapshot.stage_name = str(stage.get("name") or "种子")
        snapshot.week_key = current_week
        snapshot.week_points = week_points
        snapshot.coverage_json = coverage
        snapshot.dimensions_json = dimensions
        snapshot.source_revision = source_revision
        snapshot.computed_at = utcnow()
        session.flush()

    return {
        "student_id": student_id,
        "term_id": term_id,
        "rule_version": rule.code,
        "term_points": term_points,
        "legacy_points": legacy_points,
        "stage_index": stage_index,
        "stage_name": str(stage.get("name") or "种子"),
        "stage_icon": stage.get("icon"),
        "stage_min": int(stage.get("min", 0)),
        "next_stage": rules.next_stage_for(term_points, stages),
        "stage_progress": round(rules.stage_progress(term_points, stages), 4),
        "week_key": current_week,
        "week_points": week_points,
        "stages": [dict(item) for item in stages],
        "coverage": coverage,
        "dimensions": dimensions,
        "source_revision": source_revision,
        "computed_at": utcnow().isoformat(),
    }


def read_snapshot(session, *, student_id: int, term_id: int) -> dict[str, Any] | None:
    """读取已持久化快照（不重算），用于批量展示。"""
    snapshot = session.scalar(select(StudentGrowthSnapshot).where(
        StudentGrowthSnapshot.student_id == student_id,
        StudentGrowthSnapshot.term_id == term_id,
    ))
    if snapshot is None:
        return None
    return {
        "student_id": snapshot.student_id,
        "term_id": snapshot.term_id,
        "rule_version": snapshot.rule_version,
        "term_points": snapshot.term_points,
        "legacy_points": snapshot.legacy_points,
        "stage_index": snapshot.stage_index,
        "stage_name": snapshot.stage_name,
        "week_key": snapshot.week_key,
        "week_points": snapshot.week_points,
        "coverage": snapshot.coverage_json or {},
        "dimensions": snapshot.dimensions_json or {},
        "source_revision": snapshot.source_revision,
        "computed_at": snapshot.computed_at.isoformat() if snapshot.computed_at else None,
    }


def stale_student_ids(session, *, term_id: int, student_ids: list[int]) -> list[int]:
    """返回事件指纹与快照不一致（或没有快照）的学生 ID。"""
    stale: list[int] = []
    for student_id in student_ids:
        snapshot = session.scalar(select(StudentGrowthSnapshot).where(
            StudentGrowthSnapshot.student_id == student_id,
            StudentGrowthSnapshot.term_id == term_id,
        ))
        revision = build_snapshot(
            session, student_id=student_id, term_id=term_id, persist=False)["source_revision"]
        if snapshot is None or snapshot.source_revision != revision:
            stale.append(student_id)
    return stale
