"""学习事件账本：幂等写入、按学生/学期重建奖励。

一个活动同时来自自动同步和教师补录时，按规范化来源键合并；重试同一请求
不得新增积分。更正不删除原记录，而是追加一条引用原事件的撤销事件。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, select

from ...models import GrowthAward, GrowthEvent, GrowthRuleVersion, GrowthTermRule, StudentGrowthSnapshot
from ...models.entities import utcnow
from . import rules


def _term_rule_code(session, term_id: int | None) -> str | None:
    if term_id is None:
        return None
    bound = session.get(GrowthTermRule, term_id)
    if bound is not None:
        return bound.rule_code
    from_snapshot = session.scalar(select(StudentGrowthSnapshot.rule_version).where(
        StudentGrowthSnapshot.term_id == term_id,
    ).order_by(StudentGrowthSnapshot.id).limit(1))
    if from_snapshot:
        return from_snapshot
    return session.scalar(select(GrowthAward.rule_version).where(
        GrowthAward.term_id == term_id,
    ).order_by(GrowthAward.id).limit(1))


def ensure_rule_version(session, *, term_id: int | None = None) -> GrowthRuleVersion:
    """返回当前生效的规则版本；不存在时创建内置默认版本。"""
    code = _term_rule_code(session, term_id) or rules.DEFAULT_RULE_CODE
    rule = session.scalar(select(GrowthRuleVersion).where(GrowthRuleVersion.code == code))
    if rule is None:
        if code != rules.DEFAULT_RULE_CODE:
            raise LookupError(f"学期绑定的成长规则版本不存在：{code}")
        rule = rules.build_default_rule_version()
        session.add(rule)
        session.flush()
    if term_id is not None and session.get(GrowthTermRule, term_id) is None:
        session.add(GrowthTermRule(term_id=term_id, rule_code=rule.code))
        session.flush()
    return rule


def read_rule_version(session, *, term_id: int | None = None) -> GrowthRuleVersion:
    """Return the effective rule for a GET without creating a database row."""
    code = _term_rule_code(session, term_id) or rules.DEFAULT_RULE_CODE
    found = session.scalar(select(GrowthRuleVersion).where(GrowthRuleVersion.code == code))
    if found is not None:
        return found
    if code != rules.DEFAULT_RULE_CODE:
        raise LookupError(f"学期绑定的成长规则版本不存在：{code}")
    return rules.build_default_rule_version()


def make_idempotency_key(*parts: Any) -> str:
    raw = json.dumps([str(part) for part in parts], ensure_ascii=False,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def find_by_idempotency_key(session, key: str) -> GrowthEvent | None:
    """按幂等键查找既有事件；命中说明该请求已处理过。"""
    return session.scalar(select(GrowthEvent).where(
        GrowthEvent.idempotency_key == key))


def record_event(
    session,
    *,
    student_id: int,
    term_id: int,
    event_type: str,
    occurred_at: datetime,
    class_id_at_event: int | None = None,
    source_type: str = "teacher",
    source_id: str | None = None,
    source_revision: str | None = None,
    business_date: date | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = "teacher",
    reverses_event_id: int | None = None,
    proposed_points: int | None = None,
    idempotency_key: str | None = None,
) -> GrowthEvent:
    """写入一条学习事件；相同幂等键已存在时返回原事件（不重复计分）。"""
    rule = ensure_rule_version(session, term_id=term_id)
    if idempotency_key is None:
        idempotency_key = make_idempotency_key(
            "growth-event", student_id, term_id, event_type,
            source_type, source_id, source_revision,
            business_date.isoformat() if business_date else occurred_at.date().isoformat(),
        )
    existing = find_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return existing

    event_payload = dict(payload or {})
    if proposed_points is not None:
        event_payload["proposed_points"] = int(proposed_points)

    if reverses_event_id is not None:
        original = session.get(GrowthEvent, reverses_event_id)
        if original is None:
            raise LookupError("要撤销的事件不存在")
        if original.term_id != term_id or original.student_id != student_id:
            raise LookupError("撤销事件必须与原事件属于同一学生和学期")
        # 撤销按原事件的业务日期释放额度，使重算结果与发生顺序一致。
        business_date = original.business_date
        class_id_at_event = class_id_at_event or original.class_id_at_event

    if business_date is None:
        business_date = rules.business_date_for(occurred_at, rule.timezone)

    event = GrowthEvent(
        student_id=student_id,
        term_id=term_id,
        class_id_at_event=class_id_at_event,
        source_type=source_type,
        source_id=source_id,
        source_revision=source_revision,
        event_type=event_type,
        occurred_at=occurred_at,
        recorded_at=utcnow(),
        business_date=business_date,
        payload_json=event_payload,
        actor=actor,
        reverses_event_id=reverses_event_id,
        idempotency_key=idempotency_key,
    )
    session.add(event)
    session.flush()
    return event


def rebuild_awards(session, *, student_id: int, term_id: int,
                   rule: GrowthRuleVersion | None = None) -> list[GrowthAward]:
    """按稳定顺序重算该学生本学期的全部奖励（可重放）。

    写入新事件或撤销后调用。只重算受影响的学生/学期，不在每次页面渲染时
    遍历全班所有历史考试。
    """
    rule = rule or ensure_rule_version(session, term_id=term_id)
    computed = calculate_awards(session, student_id=student_id, term_id=term_id,
                                rule=rule)

    session.execute(delete(GrowthAward).where(
        GrowthAward.student_id == student_id,
        GrowthAward.term_id == term_id,
    ))
    session.flush()
    rows: list[GrowthAward] = []
    for item in computed:
        row = GrowthAward(
            event_id=item.event_id,
            student_id=student_id,
            term_id=term_id,
            rule_version=rule.code,
            business_date=item.business_date,
            week_key=item.week_key,
            proposed_points=item.proposed_points,
            applied_points=item.applied_points,
            cap_reason=item.cap_reason,
            calculation_revision=1,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def calculate_awards(session, *, student_id: int, term_id: int,
                     rule: GrowthRuleVersion | None = None) -> list[Any]:
    """Compute the current projection without mutating the ledger or awards."""
    rule = rule or read_rule_version(session, term_id=term_id)
    events = list(session.scalars(
        select(GrowthEvent)
        .where(GrowthEvent.student_id == student_id, GrowthEvent.term_id == term_id)
        .order_by(GrowthEvent.business_date, GrowthEvent.occurred_at, GrowthEvent.id)
    ))
    payloads = [{
        "id": event.id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "business_date": event.business_date,
        "proposed_points": (event.payload_json or {}).get("proposed_points"),
        "progress_points": (event.payload_json or {}).get("progress_points"),
        "teacher_confirmed": (event.source_type == "teacher"
                              and (event.payload_json or {}).get("scoring_mode")
                              == "teacher_confirmed_v1"),
        "reverses_event_id": event.reverses_event_id,
    } for event in events]
    computed = rules.compute_awards(payloads, rule=rule)
    return [SimpleNamespace(**item) for item in computed if item["event_id"] is not None]


def list_events(session, *, student_id: int, term_id: int) -> list[GrowthEvent]:
    return list(session.scalars(
        select(GrowthEvent)
        .where(GrowthEvent.student_id == student_id, GrowthEvent.term_id == term_id)
        .order_by(GrowthEvent.business_date.desc(), GrowthEvent.occurred_at.desc(),
                  GrowthEvent.id.desc())
    ))


def list_awards(session, *, student_id: int, term_id: int) -> list[GrowthAward]:
    return list(session.scalars(
        select(GrowthAward)
        .where(GrowthAward.student_id == student_id, GrowthAward.term_id == term_id)
        .order_by(GrowthAward.business_date, GrowthAward.id)
    ))


def events_source_revision(events: list[GrowthEvent], awards: list[GrowthAward]) -> str:
    """事件 + 奖励的稳定指纹，用于判断快照是否需要重建。"""
    payload = {
        "events": [[event.id, event.event_type, event.business_date.isoformat(),
                    event.reverses_event_id, event.source_revision,
                    event.payload_json or {}]
                   for event in events],
        "awards": [[award.event_id, award.applied_points, award.cap_reason]
                   for award in awards],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
