"""旧版（Windows 参考包）成长记录导入：预览与确认（方案 §7）。

原则：
- 不覆盖 Windows 包内文件，也不通过新公式静默重算已展示过的历史奖励。
- 手工旧记录只有日期、分值、备注，按「迁移批次＋原学期＋学号＋原位置」生成稳定
  迁移键，保留原值与原文；两条完全相同的记录不会被自动判成同一事件。
- 缺日期的记录只作为旧版累计依据，不擅自分配到某一天；确认导入时可显式提供
  教师确认的结转日，否则列为跳过。
- 旧总值保留为「历史营养（旧规则）」（``legacy_manual`` 事件不进入学期营养）。
- 导入幂等、可整批撤销；预览阶段只读，不写库。

输入契约（``payload``）::

    {
      "source": "windows-forest",
      "source_term": "2025-2026-1",          # 原学期标签，用于迁移键
      "batch_label": "2026-09-25 迁移",
      # 参考包结构：state.forest.logs，键为学生标识（常为学号）
      "logs": {"8": [{"date": "2026-09-01", "pts": 3, "note": "作业按时完成"}]},
      # 或扁平记录（等价）：records: [{student_key, date, pts, note}]
      "legacy_totals": {"8": 42},            # 可选：旧版计算总分，用于差异对照
      "legacy_auto_awards": [                # 可选：旧版考试/默写自动奖励，用于异常检测
        {"student_key": "8", "kind": "exam", "score": null, "full": 100,
         "awarded_points": 5, "label": "期中英语"}
      ]
    }
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import select

from ...models import Enrollment, GrowthEvent, Student
from . import events as growth_events
from . import snapshots

SOURCE = "windows-forest"
# 参考包单次补录上限（正负同限）。
MAX_ABS_POINTS = 50
# 视为「未取到成绩」的空值集合。
_BLANK = (None, "", "—", "-", "缺考", "absent", "excused", "null", "None")


# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("/", "-")[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def _coerce_points(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _record_fields(raw: Any) -> dict[str, Any]:
    """把一条旧记录归一化为 {points, note, date, date_raw}。"""
    if not isinstance(raw, dict):
        return {"points": 0, "note": "", "date": None, "date_raw": None}
    points = _coerce_points(raw.get("pts", raw.get("points")))
    note = str(raw.get("note") or "").strip()
    date_raw = raw.get("date", raw.get("business_date"))
    return {"points": points, "note": note, "date": _parse_date(date_raw), "date_raw": date_raw}


def _iter_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """展开 logs / records 为带学生键与原始位置的扁平记录。"""
    records: list[dict[str, Any]] = []
    logs = payload.get("logs")
    if isinstance(logs, dict):
        for key, entries in logs.items():
            if not isinstance(entries, list):
                continue
            for position, entry in enumerate(entries):
                records.append({"student_key": str(key), "position": position, "raw": entry})
    flat = payload.get("records")
    if isinstance(flat, list):
        for position, entry in enumerate(flat):
            if not isinstance(entry, dict):
                continue
            key = entry.get("student_key", entry.get("student_no", entry.get("student_id")))
            records.append({
                "student_key": str(key) if key is not None else "",
                "position": position,
                "raw": entry,
            })
    return records


# --------------------------------------------------------------------------- #
# 身份映射：优先学号，其次数据库主键
# --------------------------------------------------------------------------- #
def _build_maps(session, *, term_id: int) -> tuple[dict[str, tuple], dict[str, list[tuple]]]:
    rows = session.execute(
        select(Student, Enrollment)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .where(Enrollment.term_id == term_id, Enrollment.status == "active")
    ).all()
    by_id: dict[str, tuple] = {}
    by_no: dict[str, list[tuple]] = {}
    for student, enrollment in rows:
        by_id[str(student.id)] = (student, enrollment)
        if student.student_no:
            by_no.setdefault(str(student.student_no), []).append((student, enrollment))
    return by_id, by_no


def _resolve(key: str, by_id: dict, by_no: dict) -> tuple[Any, Any, str]:
    """返回 (student, enrollment, method)；method 也用于说明未映射原因。"""
    matches = by_no.get(key)
    if matches:
        if len(matches) == 1:
            return matches[0][0], matches[0][1], "student_no"
        return None, None, "ambiguous_student_no"
    hit = by_id.get(key)
    if hit is not None:
        return hit[0], hit[1], "id"
    return None, None, "unmapped"


# --------------------------------------------------------------------------- #
# 预览（只读）
# --------------------------------------------------------------------------- #
def preview_import(session, *, term_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """只读预览：学生映射、旧总分、异常、重复候选与无法识别来源。"""
    by_id, by_no = _build_maps(session, term_id=term_id)
    records = _iter_records(payload)
    legacy_totals = payload.get("legacy_totals") if isinstance(payload.get("legacy_totals"), dict) else {}

    students: dict[int, dict[str, Any]] = {}
    unmapped: dict[str, dict[str, Any]] = {}
    anomalies: list[dict[str, Any]] = []
    signatures: dict[tuple, list[dict[str, Any]]] = {}
    dated = 0
    undated = 0

    for record in records:
        fields = _record_fields(record["raw"])
        key = record["student_key"]
        if not key:
            unmapped.setdefault("", {"student_key": "", "record_count": 0, "manual_total": 0,
                                     "reason": "missing_student_key"})
            unmapped[""]["record_count"] += 1
            unmapped[""]["manual_total"] += fields["points"]
            continue

        student, enrollment, method = _resolve(key, by_id, by_no)
        if student is None:
            bucket = unmapped.setdefault(key, {"student_key": key, "record_count": 0,
                                               "manual_total": 0, "reason": method})
            bucket["record_count"] += 1
            bucket["manual_total"] += fields["points"]
            continue

        entry = students.setdefault(student.id, {
            "student_key": key,
            "student_id": student.id,
            "student_no": student.student_no,
            "name": student.name,
            "class_id": enrollment.class_id,
            "mapped_by": method,
            "record_count": 0,
            "manual_add": 0,
            "manual_ded": 0,
            "manual_total": 0,
            "dated_count": 0,
            "undated_count": 0,
        })
        entry["record_count"] += 1
        entry["manual_total"] += fields["points"]
        if fields["points"] > 0:
            entry["manual_add"] += fields["points"]
        else:
            entry["manual_ded"] += fields["points"]

        if fields["date"] is None:
            undated += 1
            entry["undated_count"] += 1
            anomalies.append({
                "type": "missing_date", "student_key": key, "student_id": student.id,
                "position": record["position"], "points": fields["points"],
                "note": fields["note"],
                "detail": "缺日期，仅作旧版累计依据，不分配到某一天",
            })
        else:
            dated += 1
            entry["dated_count"] += 1

        if fields["points"] == 0:
            anomalies.append({
                "type": "zero_points", "student_key": key, "student_id": student.id,
                "position": record["position"], "note": fields["note"],
                "detail": "0 分记录保留原值，但不改变历史营养",
            })
        if abs(fields["points"]) > MAX_ABS_POINTS:
            anomalies.append({
                "type": "oversize_points", "student_key": key, "student_id": student.id,
                "position": record["position"], "points": fields["points"],
                "detail": f"单条超过 {MAX_ABS_POINTS} 点，请人工核对",
            })

        signature = (student.id, fields["date"].isoformat() if fields["date"] else None,
                     fields["points"], fields["note"])
        signatures.setdefault(signature, []).append({
            "student_key": key, "position": record["position"], "points": fields["points"],
        })

    for (student_id, day, points, note), items in signatures.items():
        if len(items) > 1:
            anomalies.append({
                "type": "duplicate_candidates", "student_id": student_id,
                "date": day, "points": points, "note": note,
                "positions": [item["position"] for item in items],
                "detail": "内容完全相同的候选，不会被自动合并，请人工确认",
            })

    student_rows: list[dict[str, Any]] = []
    for entry in students.values():
        old_total = legacy_totals.get(entry["student_key"])
        old_total = _coerce_points(old_total) if old_total is not None else None
        student_rows.append({
            **entry,
            "old_total": old_total,
            "difference": (old_total - entry["manual_total"]) if old_total is not None else None,
        })
    student_rows.sort(key=lambda item: (item["student_no"] or "", item["student_id"]))

    auto_eval = _evaluate_auto_awards(payload.get("legacy_auto_awards"), by_id, by_no)

    total_add = sum(item["manual_add"] for item in student_rows)
    total_ded = sum(item["manual_ded"] for item in student_rows)
    return {
        "source": str(payload.get("source") or SOURCE),
        "source_term": str(payload.get("source_term") or payload.get("term") or ""),
        "batch_label": str(payload.get("batch_label") or ""),
        "term_id": term_id,
        "totals": {
            "record_count": len(records),
            "students_mapped": len(student_rows),
            "students_unmapped": len(unmapped),
            "dated_records": dated,
            "undated_records": undated,
            "manual_add": total_add,
            "manual_ded": total_ded,
            "manual_total": total_add + total_ded,
        },
        "students": student_rows,
        "unmapped_students": sorted(unmapped.values(), key=lambda item: item["student_key"]),
        "anomalies": anomalies,
        "auto_awards": auto_eval,
        "can_confirm": True,
    }


def _evaluate_auto_awards(raw: Any, by_id: dict, by_no: dict) -> dict[str, Any]:
    """旧版考试/默写自动奖励的异常检测（空分/缺考也加分、负值）。"""
    if not isinstance(raw, list):
        return {"evaluated": False, "anomalies": [],
                "note": "未提供旧版自动奖励明细，未评估空分/缺考误奖"}
    anomalies: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        key = str(item.get("student_key", item.get("student_no", item.get("student_id")) or ""))
        student, _enrollment, _method = _resolve(key, by_id, by_no)
        awarded = _coerce_points(item.get("awarded_points"))
        score = item.get("score")
        blank = score is None or (isinstance(score, str) and score.strip() in _BLANK)
        if blank and awarded > 0:
            anomalies.append({
                "type": "blank_or_absent_awarded", "student_key": key,
                "student_id": student.id if student else None,
                "index": index, "awarded_points": awarded,
                "kind": item.get("kind"), "label": item.get("label"),
                "detail": "空分或缺考仍获得奖励，需人工核对",
            })
        if awarded < 0:
            anomalies.append({
                "type": "negative_award", "student_key": key,
                "student_id": student.id if student else None,
                "index": index, "awarded_points": awarded,
                "detail": "负奖励会使历史营养倒退，建议改为撤销或待办",
            })
    return {"evaluated": True, "anomalies": anomalies, "note": ""}


# --------------------------------------------------------------------------- #
# 确认导入（写入）
# --------------------------------------------------------------------------- #
def confirm_import(
    session,
    *,
    term_id: int,
    payload: dict[str, Any],
    actor: str = "teacher",
    batch_id: str | None = None,
    carry_over_date: Any = None,
) -> dict[str, Any]:
    """写入旧记录为 ``legacy_manual`` 事件；幂等、可整批撤销。

    相同 ``batch_id`` 重试不会重复计分（迁移键绑定批次）。缺日期记录仅在提供
    ``carry_over_date``（教师确认的结转日）时按学生聚合成一条历史依据。
    """
    batch_id = batch_id or uuid.uuid4().hex
    source_term = str(payload.get("source_term") or payload.get("term") or "")
    carry_day = _parse_date(carry_over_date) if carry_over_date is not None else None

    by_id, by_no = _build_maps(session, term_id=term_id)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    undated: dict[int, dict[str, Any]] = {}

    for record in _iter_records(payload):
        fields = _record_fields(record["raw"])
        key = record["student_key"]
        student, enrollment, method = _resolve(key, by_id, by_no)
        if student is None:
            skipped.append({"student_key": key, "position": record["position"],
                            "reason": f"无法映射学生（{method}）"})
            continue

        if fields["date"] is None:
            if carry_day is None:
                skipped.append({"student_key": key, "position": record["position"],
                                "reason": "缺日期且未提供结转日，未导入"})
                continue
            bucket = undated.setdefault(student.id, {
                "student": student, "enrollment": enrollment, "total": 0, "count": 0,
                "notes": [],
            })
            bucket["total"] += fields["points"]
            bucket["count"] += 1
            if fields["note"]:
                bucket["notes"].append(fields["note"])
            continue

        idempotency_key = growth_events.make_idempotency_key(
            "growth-legacy", batch_id, source_term, key, record["position"])
        existing = growth_events.find_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            skipped.append({"student_key": key, "position": record["position"],
                            "reason": "同一批次已导入", "event_id": existing.id})
            continue

        event = growth_events.record_event(
            session,
            student_id=student.id,
            term_id=term_id,
            event_type="legacy_manual",
            occurred_at=datetime.combine(fields["date"], time(0, 0), tzinfo=timezone.utc),
            class_id_at_event=enrollment.class_id,
            source_type="migration",
            source_id=batch_id,
            source_revision=source_term or None,
            business_date=fields["date"],
            payload={
                "note": fields["note"],
                "legacy_points": fields["points"],
                "migration_batch": batch_id,
                "source_student_key": key,
                "position": record["position"],
                "date_missing": False,
            },
            actor=actor,
            idempotency_key=idempotency_key,
        )
        created.append({
            "event_id": event.id, "student_id": student.id, "student_key": key,
            "position": record["position"], "points": fields["points"],
            "business_date": fields["date"].isoformat(),
        })

    for student_id, bucket in undated.items():
        day = carry_day
        idempotency_key = growth_events.make_idempotency_key(
            "growth-legacy-undated", batch_id, source_term, student_id)
        existing = growth_events.find_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            skipped.append({"student_key": str(student_id), "position": None,
                            "reason": "同一批次已导入", "event_id": existing.id})
            continue
        event = growth_events.record_event(
            session,
            student_id=student_id,
            term_id=term_id,
            event_type="legacy_manual",
            occurred_at=datetime.combine(day, time(0, 0), tzinfo=timezone.utc),
            class_id_at_event=bucket["enrollment"].class_id,
            source_type="migration",
            source_id=batch_id,
            source_revision=source_term or None,
            business_date=day,
            payload={
                "note": "；".join(bucket["notes"][:5]),
                "legacy_points": bucket["total"],
                "migration_batch": batch_id,
                "source_student_key": str(student_id),
                "undated_count": bucket["count"],
                "date_missing": True,
            },
            actor=actor,
            idempotency_key=idempotency_key,
        )
        created.append({
            "event_id": event.id, "student_id": student_id,
            "position": None, "points": bucket["total"],
            "business_date": day.isoformat(), "aggregated_undated": bucket["count"],
        })

    affected = sorted({item["student_id"] for item in created})
    for student_id in affected:
        snapshots.build_snapshot(session, student_id=student_id, term_id=term_id)

    return {
        "batch_id": batch_id,
        "source_term": source_term,
        "carry_over_date": carry_day.isoformat() if carry_day else None,
        "created": created,
        "skipped": skipped,
        "affected_students": affected,
        "preview": preview_import(session, term_id=term_id, payload=payload),
    }


def reverse_batch(session, *, term_id: int, batch_id: str, reason: str = "整批撤销导入",
                  actor: str = "teacher") -> dict[str, Any]:
    """整批撤销一次旧版导入：为批次内每条记录追加撤销事件（不删除原记录）。"""
    originals = list(session.scalars(
        select(GrowthEvent).where(
            GrowthEvent.term_id == term_id,
            GrowthEvent.source_type == "migration",
            GrowthEvent.source_id == batch_id,
            GrowthEvent.event_type == "legacy_manual",
        ).order_by(GrowthEvent.id)
    ))
    reversed_ids: list[int] = []
    for original in originals:
        already = session.scalar(select(GrowthEvent).where(
            GrowthEvent.reverses_event_id == original.id))
        if already is not None:
            continue
        reversal = growth_events.record_event(
            session,
            student_id=original.student_id,
            term_id=term_id,
            event_type="reversal",
            occurred_at=datetime.now(timezone.utc),
            class_id_at_event=original.class_id_at_event,
            source_type="correction",
            source_id=str(original.id),
            payload={"note": reason, "reverses_event_id": original.id},
            actor=actor,
            reverses_event_id=original.id,
            idempotency_key=growth_events.make_idempotency_key(
                "growth-reversal", original.id),
        )
        reversed_ids.append(original.id)
    for student_id in {item.student_id for item in originals}:
        snapshots.build_snapshot(session, student_id=student_id, term_id=term_id)
    return {"batch_id": batch_id, "reversed_event_ids": reversed_ids,
            "reversed_count": len(reversed_ids)}
