"""成长树高层编排（路由层调用）。

范围校验、输入转换、输出契约都在这里；计分公式只在 ``rules`` 里写一份。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ...models import Class, Enrollment, GrowthEvent, Student, StudentGrowthSnapshot, Term
from ...models.entities import utcnow
from . import events as growth_events
from . import performance, rules, snapshots


class GrowthScopeError(ValueError):
    """范围校验失败（学生不属于当前学期/班级等）。"""


def _enrollment_map(session, *, term_id: int, class_id: int | None = None,
                    student_ids: list[int] | None = None) -> dict[int, Enrollment]:
    stmt = (
        select(Enrollment)
        .where(Enrollment.term_id == term_id, Enrollment.status == "active")
    )
    if class_id is not None:
        stmt = stmt.where(Enrollment.class_id == class_id)
    if student_ids is not None:
        stmt = stmt.where(Enrollment.student_id.in_(student_ids))
    return {item.student_id: item for item in session.scalars(stmt)}


def require_student_in_term(session, *, student_id: int, term_id: int,
                            class_id: int | None = None) -> Enrollment:
    enrollment = session.scalar(select(Enrollment).where(
        Enrollment.term_id == term_id,
        Enrollment.student_id == student_id,
        Enrollment.status == "active",
    ))
    if enrollment is None:
        raise GrowthScopeError("学生不属于当前学期")
    if class_id is not None and enrollment.class_id != class_id:
        raise GrowthScopeError("学生不属于当前班级")
    return enrollment


def forest_rows(session, *, term_id: int, class_id: int | None = None) -> dict[str, Any]:
    """班级森林页数据：每名学生一张卡片所需的数值（全部后端确定性输出）。"""
    rule = growth_events.read_rule_version(session, term_id=term_id)
    stmt = (
        select(Student, Enrollment, Class)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .join(Class, Class.id == Enrollment.class_id)
        .where(Enrollment.term_id == term_id, Enrollment.status == "active")
        .order_by(Student.student_no, Student.id)
    )
    if class_id is not None:
        stmt = stmt.where(Enrollment.class_id == class_id)
    rows = session.execute(stmt).all()

    students: list[dict[str, Any]] = []
    for student, enrollment, classroom in rows:
        snapshot = snapshots.build_snapshot(
            session, student_id=student.id, term_id=term_id, rule=rule,
            persist=False)
        students.append({
            "student_id": student.id,
            "student_no": student.student_no,
            "name": student.name,
            "class_id": enrollment.class_id,
            "class_name": classroom.name,
            "term_points": snapshot["term_points"],
            "legacy_points": snapshot["legacy_points"],
            "stage_index": snapshot["stage_index"],
            "stage_name": snapshot["stage_name"],
            "stage_icon": snapshot["stage_icon"],
            "stage_min": snapshot["stage_min"],
            "next_stage": snapshot["next_stage"],
            "stage_progress": snapshot["stage_progress"],
            "week_points": snapshot["week_points"],
            "coverage": snapshot["coverage"],
            "dimensions": {
                key: {"status": value.get("status")}
                for key, value in snapshot["dimensions"].items()
            },
            "source_revision": snapshot["source_revision"],
        })

    total = sum(item["term_points"] for item in students)
    return {
        "term_id": term_id,
        "class_id": class_id,
        "rule_version": rule.code,
        "stages": [dict(item) for item in (rule.stage_thresholds_json or rules.DEFAULT_STAGES)],
        "summary": {
            "student_count": len(students),
            "total_points": total,
            "average_points": round(total / len(students), 1) if students else 0,
            "active_this_week": sum(1 for item in students if item["week_points"] > 0),
            "blossomed_count": sum(1 for item in students if item["stage_index"] >= 4),
        },
        "students": students,
    }


def student_detail(session, *, term_id: int, student_id: int,
                   class_id: int | None = None) -> dict[str, Any]:
    """单人成长明细：阶段、来源明细、五个分支、待订正与历史年轮。"""
    enrollment = require_student_in_term(
        session, student_id=student_id, term_id=term_id, class_id=class_id)
    student = session.get(Student, student_id)
    classroom = session.get(Class, enrollment.class_id)

    snapshot = snapshots.build_snapshot(session, student_id=student_id, term_id=term_id,
                                        persist=False)
    events = growth_events.list_events(session, student_id=student_id, term_id=term_id)
    awards = {award.event_id: award for award in growth_events.calculate_awards(
        session, student_id=student_id, term_id=term_id)}

    records: list[dict[str, Any]] = []
    for event in events:
        award = awards.get(event.id)
        payload = event.payload_json or {}
        records.append({
            "event_id": event.id,
            "event_type": event.event_type,
            "event_label": rules.DEFAULT_EVENT_RULES.get(event.event_type, {}).get("label", event.event_type),
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "recorded_at": event.recorded_at.isoformat() if event.recorded_at else None,
            "business_date": event.business_date.isoformat(),
            "source_type": event.source_type,
            "source_id": event.source_id,
            "note": payload.get("note"),
            "legacy_points": payload.get("legacy_points"),
            # 进步分拆解（仅考试自动事件携带）：本次得分率、上一次可比基线。
            "progress_points": payload.get("progress_points"),
            "score_rate": payload.get("score_rate"),
            "baseline_rate": payload.get("baseline_rate"),
            "exam_id": payload.get("exam_id"),
            "actor": event.actor,
            "reverses_event_id": event.reverses_event_id,
            "proposed_points": award.proposed_points if award else None,
            "applied_points": award.applied_points if award else 0,
            "cap_reason": award.cap_reason if award else None,
            "reversible": (
                event.event_type != "reversal"
                and not any(other.reverses_event_id == event.id for other in events)
            ),
        })

    # 待订正：作业/默写未订正等待办（当前无任务账本时为空，明确显示而不是编造）。
    pending_corrections: list[dict[str, Any]] = []

    history: list[dict[str, Any]] = []
    other_snapshots = session.execute(
        select(StudentGrowthSnapshot, Term)
        .join(Term, Term.id == StudentGrowthSnapshot.term_id)
        .where(
            StudentGrowthSnapshot.student_id == student_id,
            StudentGrowthSnapshot.term_id != term_id,
        )
        .order_by(StudentGrowthSnapshot.term_id.desc())
    ).all()
    for other, term in other_snapshots:
        history.append({
            "term_id": term.id,
            "term_name": term.name,
            "term_points": other.term_points,
            "stage_name": other.stage_name,
        })

    return {
        "student_id": student_id,
        "student_no": student.student_no if student else None,
        "name": student.name if student else None,
        "class_id": enrollment.class_id,
        "class_name": classroom.name if classroom else None,
        "term_id": term_id,
        "snapshot": snapshot,
        "records": records,
        "pending_corrections": pending_corrections,
        "history": history,
    }


def record_batch(session, *, term_id: int, items: list[dict[str, Any]],
                 actor: str = "teacher", request_id: str | None = None) -> dict[str, Any]:
    """批量补录学习事件（含加分与撤销外的更正）。

    每条事件的幂等键绑定 ``request_id`` + 序号：重试同一请求不会重复计分。
    """
    request_id = request_id or uuid.uuid4().hex
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    now = utcnow()
    term_rule = growth_events.ensure_rule_version(session, term_id=term_id)

    for index, item in enumerate(items):
        event_type = str(item.get("event_type") or "").strip()
        student_id = item.get("student_id")
        if event_type not in rules.MANUAL_EVENT_TYPES:
            skipped.append({"index": index, "reason": "不支持的事件类型"})
            continue
        if event_type not in (term_rule.event_rules_json or {}):
            skipped.append({"index": index, "reason": "当前学期规则不支持该事件类型"})
            continue
        if not isinstance(student_id, int):
            skipped.append({"index": index, "reason": "缺少学生 ID"})
            continue
        note = str(item.get("note") or "").strip()
        if not note:
            skipped.append({"index": index, "reason": "补录必须填写事由"})
            continue
        try:
            enrollment = require_student_in_term(
                session, student_id=student_id, term_id=term_id)
        except GrowthScopeError as exc:
            skipped.append({"index": index, "reason": str(exc)})
            continue

        occurred_at = _parse_datetime(item.get("occurred_at")) or now
        proposed = item.get("points")
        if event_type == "teacher_bonus":
            proposed = max(1, min(3, int(proposed or 1)))
        elif event_type == "teacher_observation":
            proposed = max(1, min(2, int(proposed or 1)))
        else:
            proposed = None

        idempotency_key = growth_events.make_idempotency_key(
            "growth-batch", request_id, index)
        existing = growth_events.find_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            skipped.append({"index": index, "reason": "重复请求，已存在",
                            "event_id": existing.id, "student_id": student_id})
            continue

        event = growth_events.record_event(
            session,
            student_id=student_id,
            term_id=term_id,
            event_type=event_type,
            occurred_at=occurred_at,
            class_id_at_event=enrollment.class_id,
            source_type="teacher",
            source_id=request_id,
            payload={"note": note, "request_id": request_id},
            actor=actor,
            proposed_points=proposed,
            idempotency_key=idempotency_key,
        )
        created.append({
            "index": index,
            "event_id": event.id,
            "student_id": student_id,
            "event_type": event_type,
        })

    for student_id in {item["student_id"] for item in created}:
        snapshots.build_snapshot(session, student_id=student_id, term_id=term_id)

    return {"request_id": request_id, "created": created, "skipped": skipped}


def reverse_event(session, *, term_id: int, event_id: int, reason: str,
                  actor: str = "teacher") -> dict[str, Any]:
    """撤销一条误录事件：追加引用原事件的撤销事件，不删除原记录。"""
    original = session.get(GrowthEvent, event_id)
    if original is None or original.term_id != term_id:
        raise GrowthScopeError("事件不存在或不属于当前学期")
    if original.event_type == "reversal":
        raise GrowthScopeError("撤销事件本身不能再被撤销")
    existing = session.scalar(select(GrowthEvent).where(
        GrowthEvent.reverses_event_id == original.id))
    if existing is not None:
        raise GrowthScopeError("该记录已被撤销，不能重复撤销")

    reversal = growth_events.record_event(
        session,
        student_id=original.student_id,
        term_id=term_id,
        event_type="reversal",
        occurred_at=utcnow(),
        class_id_at_event=original.class_id_at_event,
        source_type="correction",
        source_id=str(original.id),
        payload={"note": reason or "误录撤销", "reverses_event_id": original.id},
        actor=actor,
        reverses_event_id=original.id,
        idempotency_key=growth_events.make_idempotency_key(
            "growth-reversal", original.id),
    )
    snapshot = snapshots.build_snapshot(
        session, student_id=original.student_id, term_id=term_id)
    return {"reversal_event_id": reversal.id, "snapshot": snapshot}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
