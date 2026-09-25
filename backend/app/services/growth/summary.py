"""成长事实摘要（方案 §6）：只输出后端确定性事实，供画像与分析包引用。

边界（方案 §6.2）：

* 积分、阶段、覆盖率、来源修订号由后端确定性计算，模型只读不写；
* 缺证据的维度显式标注 ``no_evidence`` / ``insufficient_comparable_history``，
  绝不用 0 填充，也不把「无记录」写成「表现差」；
* 本模块不调用任何模型；即使模型不可用，森林页与快照仍可独立工作。
"""

from __future__ import annotations

from typing import Any

from . import events as growth_events
from . import rules, snapshots

GROWTH_SUMMARY_SCHEMA_VERSION = "1.0"
# 证据引用上限：分析包只冻结可追溯的近期事件，避免 prompt 无上限膨胀。
MAX_EVIDENCE_REFS = 50
# 写入画像时保留的证据引用条数（比分析包更少，画像只做追溯入口）。
MAX_PROFILE_EVIDENCE_REFS = 20

# 计入「已记录的完成任务」的事件类型。
_TASK_EVENT_TYPES = frozenset({"task_completed"})
# 计入「经确认的订正」的事件类型。
_CORRECTION_EVENT_TYPES = frozenset({"correction_verified"})


def build_growth_summary(session, *, student_id: int, term_id: int) -> dict[str, Any]:
    """构造学生本学期的成长事实摘要（确定性、可追溯、不含模型输出）。"""
    rule = growth_events.read_rule_version(session, term_id=term_id)
    snapshot = snapshots.build_snapshot(
        session, student_id=student_id, term_id=term_id, rule=rule,
        persist=False)
    event_rows = growth_events.list_events(
        session, student_id=student_id, term_id=term_id)
    # 被撤销的原事件不再作为有效证据；撤销事件本身只引用原记录。
    reversed_ids = {event.reverses_event_id for event in event_rows
                    if event.reverses_event_id is not None}

    evidence_refs: list[str] = []
    task_completed = 0
    verified_corrections = 0
    for event in event_rows:
        if event.event_type == "reversal" or event.id in reversed_ids:
            continue
        evidence_refs.append(f"growth-event:{event.id}")
        if event.event_type in _TASK_EVENT_TYPES:
            task_completed += 1
        if event.event_type in _CORRECTION_EVENT_TYPES:
            verified_corrections += 1

    dimensions = {
        key: {field: (value or {}).get(field) for field in (
            "status", "value", "latest_rate", "observations",
            "distinct_dates", "span_days", "latest_date", "trend",
            "scale_source", "sample_note")}
        for key, value in (snapshot.get("dimensions") or {}).items()
    }

    limitations: list[str] = []
    for key, value in dimensions.items():
        label = rules.DIMENSION_LABELS.get(key, key)
        status = value.get("status")
        if status == "no_evidence":
            limitations.append(f"{label}暂无记录，不能判断稳定提升")
        elif status == "insufficient_comparable_history":
            limitations.append(
                f"{label}可比测评不足 {rules.MIN_COMPARABLE_OBSERVATIONS} 个不同日期，"
                "暂不判断稳定提升")
    # 当前没有学习任务账本，因此没有「应完成」的分母，只报已记录次数。
    limitations.append("尚无学习任务账本，无法给出完成率分母；只统计已记录的有效完成任务次数")
    if int(snapshot.get("legacy_points") or 0):
        limitations.append(
            f"含历史营养（旧规则）{snapshot['legacy_points']}，不参与本学期规则计分")
    limitations.append("成长积分为学习活动记录，不等于英语能力水平；纪律扣分不进入能力值")

    evidence_status = "recorded" if evidence_refs else "no_evidence"

    return {
        "schema_version": GROWTH_SUMMARY_SCHEMA_VERSION,
        "rule_version": snapshot["rule_version"],
        "term_points": snapshot["term_points"],
        "legacy_points": snapshot["legacy_points"],
        "stage": snapshot["stage_name"],
        "stage_index": snapshot["stage_index"],
        "week_points": snapshot["week_points"],
        "observed_task_completion": {
            "status": "no_task_ledger",
            "completed": task_completed,
            "eligible": None,
        },
        "verified_corrections": verified_corrections,
        "dimensions": dimensions,
        "source_revision": snapshot["source_revision"],
        "evidence_status": evidence_status,
        "evidence_refs": evidence_refs[:MAX_EVIDENCE_REFS],
        "limitations": limitations,
    }


def freeze_growth_reference(session, *, run_id: int | None, student_id: int,
                            term_id: int) -> dict[str, Any]:
    """生成分析包时冻结成长事实指纹（方案 §6.1）。

    返回摘要，并把引用写入本次运行的 ``input_summary_json``；报告落库后
    由画像服务据此回写「依据修订号」，从而在事实更正后标记画像过期。
    """
    summary = build_growth_summary(session, student_id=student_id, term_id=term_id)
    if run_id:
        from ...models.agent_entities import AnalysisRun

        run = session.get(AnalysisRun, run_id)
        if run is not None:
            payload = dict(run.input_summary_json or {})
            payload["growth_reference"] = _reference_from_summary(summary)
            run.input_summary_json = payload
            session.flush()
    return summary


def _reference_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision": summary["source_revision"],
        "rule_version": summary["rule_version"],
        "evidence_refs": summary["evidence_refs"][:MAX_PROFILE_EVIDENCE_REFS],
    }


def record_growth_reference(session, *, student_id: int, term_id: int,
                            reference: dict[str, Any]) -> dict[str, Any] | None:
    """把冻结的成长事实引用写入画像，供过期判断（方案 §6.4）。

    模型不能写这个字段：它不在 ``normalize_patch`` 的白名单里，只能由服务端
    在画像确实引用了成长事实时写入。没有画像行时不创建空画像。
    """
    from ...models.agent_entities import StudentProfile
    from ...models.entities import utcnow
    from sqlalchemy import select

    profile = session.scalar(select(StudentProfile).where(
        StudentProfile.student_id == student_id,
        StudentProfile.term_id == term_id,
    ))
    if profile is None:
        return None
    stored = {
        "revision": str(reference.get("revision") or ""),
        "rule_version": str(reference.get("rule_version") or ""),
        "evidence_refs": list(reference.get("evidence_refs") or [])[:MAX_PROFILE_EVIDENCE_REFS],
        "recorded_at": utcnow().isoformat(),
    }
    profile.growth_reference_json = stored
    session.flush()
    return stored


def growth_profile_status(session, *, student_id: int, term_id: int,
                          current_revision: str) -> dict[str, Any]:
    """既有画像相对当前成长事实的状态：absent / not_referenced / stale / current。"""
    from ...models.agent_entities import StudentProfile
    from sqlalchemy import select

    profile = session.scalar(select(StudentProfile).where(
        StudentProfile.student_id == student_id,
        StudentProfile.term_id == term_id,
    ))
    if profile is None:
        return {"status": "absent", "label": "尚无画像",
                "recorded_revision": None, "current_revision": current_revision}
    reference = profile.growth_reference_json or {}
    recorded = reference.get("revision") or None
    if not recorded:
        return {"status": "not_referenced", "label": "画像尚未引用成长事实",
                "recorded_revision": None, "current_revision": current_revision}
    current_rule = growth_events.read_rule_version(session, term_id=term_id).code
    if recorded != current_revision or reference.get("rule_version") != current_rule:
        return {"status": "stale", "label": "依据已更新",
                "recorded_revision": recorded, "current_revision": current_revision}
    return {"status": "current", "label": "依据为最新",
            "recorded_revision": recorded, "current_revision": current_revision}
