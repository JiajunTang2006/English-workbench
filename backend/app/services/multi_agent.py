"""受控多 Agent 任务组规划与合并。

本模块刻意不直接调用模型。它只负责可测试的任务图决策、学生分片、
共享作用域快照摘要和结果合并，模型调用仍由现有 AnalysisRun 执行器负责。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable


MAX_CONCURRENCY = 4
DEFAULT_CONCURRENCY = 4
DEFAULT_SHARD_SIZE = 6


@dataclass(frozen=True)
class StudentShard:
    index: int
    student_ids: tuple[int, ...]


@dataclass(frozen=True)
class AnalysisGroupPlan:
    group_type: str
    use_multi_agent: bool
    max_concurrency: int
    shard_size: int
    shards: tuple[StudentShard, ...] = field(default_factory=tuple)
    reason: str = ""


def should_use_multi_agent(
    student_count: int,
    *,
    include_exam_analysis: bool = False,
    snapshot_ready: bool = True,
    budget_ok: bool = True,
    available_slots: int = DEFAULT_CONCURRENCY,
) -> bool:
    """判断是否值得拆分为多个独立分析任务。"""
    if student_count < 1 or not snapshot_ready or not budget_ok:
        return False
    if max(1, int(available_slots)) < 2:
        return False
    return student_count >= 4 or (include_exam_analysis and student_count >= 4)


def partition_student_ids(
    student_ids: Iterable[int],
    *,
    shard_size: int = DEFAULT_SHARD_SIZE,
) -> tuple[StudentShard, ...]:
    """稳定去重并按固定大小切分学生 ID。"""
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in student_ids:
        value = int(raw)
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    size = max(1, min(int(shard_size), 20))
    return tuple(
        StudentShard(index=index, student_ids=tuple(normalized[offset: offset + size]))
        for index, offset in enumerate(range(0, len(normalized), size), start=1)
    )


def plan_student_batch(
    student_ids: Iterable[int],
    *,
    include_exam_analysis: bool = False,
    max_concurrency: int = DEFAULT_CONCURRENCY,
    shard_size: int = DEFAULT_SHARD_SIZE,
    snapshot_ready: bool = True,
    budget_ok: bool = True,
) -> AnalysisGroupPlan:
    normalized = tuple(dict.fromkeys(int(value) for value in student_ids if int(value) > 0))
    slots = max(1, min(int(max_concurrency), MAX_CONCURRENCY))
    use_multi = should_use_multi_agent(
        len(normalized),
        include_exam_analysis=include_exam_analysis,
        snapshot_ready=snapshot_ready,
        budget_ok=budget_ok,
        available_slots=slots,
    )
    if not use_multi:
        return AnalysisGroupPlan(
            group_type="exam_plus_students" if include_exam_analysis else "student_batch",
            use_multi_agent=False,
            max_concurrency=1,
            shard_size=max(1, len(normalized)),
            shards=(StudentShard(index=1, student_ids=normalized),) if normalized else (),
            reason=("学生数量不足或当前运行条件不适合并行，降级为单任务。"),
        )
    return AnalysisGroupPlan(
        group_type="exam_plus_students" if include_exam_analysis else "student_batch",
        use_multi_agent=True,
        # 每名学生对应一个独立的 AnalysisRun；并发槽位按实际学生任务数
        # 计算，而不是按分片数量计算。否则 4~6 名学生（一个分片）会被
        # 错误降为双线，无法利用四线调度能力。
        max_concurrency=min(slots, max(2, len(normalized))),
        shard_size=max(1, min(int(shard_size), 20)),
        shards=partition_student_ids(normalized, shard_size=shard_size),
        reason="独立学生诊断数量达到并行阈值，且共享作用域快照已准备。",
    )


def build_scope_snapshot(
    *,
    term_id: int,
    class_id: int | None,
    exam_id: int | None,
    student_ids: Iterable[int],
    source: str = "analysis_group",
) -> tuple[str, dict[str, Any]]:
    """创建只包含范围和版本信息的共享快照摘要。

    具体确定性统计仍由现有工具/数据库事实源计算；摘要用于让组内运行
    绑定同一输入边界，并支持重放和审计。
    """
    payload = {
        "term_id": int(term_id),
        "class_id": int(class_id) if class_id is not None else None,
        "exam_id": int(exam_id) if exam_id is not None else None,
        "student_ids": sorted({int(value) for value in student_ids if int(value) > 0}),
        "source": source,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    snapshot_id = "scope-" + hashlib.sha256(encoded).hexdigest()[:24]
    payload["snapshot_id"] = snapshot_id
    return snapshot_id, payload


def merge_structured_answers(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """确定性合并逐生结构化结果，不新增未经证据支持的结论。"""
    ordered = [item for item in results if isinstance(item, dict)]
    findings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    limitations: list[str] = []
    seen_finding_keys: set[tuple[str, str]] = set()
    seen_recommendation_keys: set[tuple[str, str]] = set()
    for item in ordered:
        for finding in item.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            key = (str(finding.get("title") or ""), str(finding.get("claim") or finding.get("description") or ""))
            if key not in seen_finding_keys:
                seen_finding_keys.add(key)
                findings.append(dict(finding))
        for recommendation in item.get("recommendations") or []:
            if not isinstance(recommendation, dict):
                continue
            key = (str(recommendation.get("action") or ""), str(recommendation.get("rationale") or ""))
            if key not in seen_recommendation_keys:
                seen_recommendation_keys.add(key)
                recommendations.append(dict(recommendation))
        for limitation in item.get("limitations") or []:
            text = str(limitation).strip()
            if text and text not in limitations:
                limitations.append(text)
    return {
        "answer_type": "student_batch_diagnosis",
        "summary": f"已合并 {len(ordered)} 个学生诊断结果；逐生结论仍以各自证据和画像草稿为准。",
        "findings": findings,
        "recommendations": recommendations,
        "limitations": limitations,
        "result_count": len(ordered),
    }
