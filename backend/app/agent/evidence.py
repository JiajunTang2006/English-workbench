"""
证据账本 (EvidenceLedger)

每条证据生成稳定 ID，记录来源、计算公式和是否含个人数据。
证据序列化给模型前进行脱敏，展示给教师时在本地恢复姓名。
所有数值结论必须可追溯到证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Evidence:
    """单条证据。"""
    evidence_id: str
    evidence_type: str  # db_metric / file_page / question / student_trend / rule_signal
    local_fact: dict[str, Any] = field(default_factory=dict)
    source_entity: str | None = None
    source_field: str | None = None
    source_file: str | None = None
    source_page: int | None = None
    source_question_no: str | None = None
    source_cell: str | None = None
    calculation_formula: str | None = None
    numerator: float | None = None
    denominator: float | None = None
    rule_id: str | None = None
    rule_version: str | None = None
    contains_personal_data: bool = False
    display_summary: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EvidenceLedger:
    """证据账本。在一次分析运行中收集和管理所有证据。"""

    def __init__(self, run_id: int | None = None):
        self._run_id = run_id
        self._evidence: dict[str, Evidence] = {}
        self._counter = 0

    def add(
        self,
        evidence_type: str,
        local_fact: dict[str, Any] | None = None,
        source_entity: str | None = None,
        source_field: str | None = None,
        source_file: str | None = None,
        source_page: int | None = None,
        source_question_no: str | None = None,
        source_cell: str | None = None,
        calculation_formula: str | None = None,
        numerator: float | None = None,
        denominator: float | None = None,
        rule_id: str | None = None,
        rule_version: str | None = None,
        contains_personal_data: bool = False,
        display_summary: str | None = None,
    ) -> str:
        """添加证据，返回稳定证据 ID。"""
        self._counter += 1
        eid = f"ev_{self._counter}"
        ev = Evidence(
            evidence_id=eid,
            evidence_type=evidence_type,
            local_fact=local_fact or {},
            source_entity=source_entity,
            source_field=source_field,
            source_file=source_file,
            source_page=source_page,
            source_question_no=source_question_no,
            source_cell=source_cell,
            calculation_formula=calculation_formula,
            numerator=numerator,
            denominator=denominator,
            rule_id=rule_id,
            rule_version=rule_version,
            contains_personal_data=contains_personal_data,
            display_summary=display_summary,
        )
        self._evidence[eid] = ev
        return eid

    def get(self, evidence_id: str) -> Evidence | None:
        """获取单条证据。"""
        return self._evidence.get(evidence_id)

    def exists(self, evidence_id: str) -> bool:
        """检查证据是否存在。用于输出忠实度校验。"""
        return evidence_id in self._evidence

    def all_ids(self) -> list[str]:
        """返回所有证据 ID。"""
        return list(self._evidence.keys())

    def for_model(self, privacy_mapper=None) -> list[dict[str, Any]]:
        """序列化给模型（脱敏后）。"""
        result = []
        for ev in self._evidence.values():
            item = {
                "evidence_id": ev.evidence_id,
                "evidence_type": ev.evidence_type,
                "fact": ev.local_fact,
                "summary": ev.display_summary,
            }
            if privacy_mapper and ev.contains_personal_data:
                item["fact"] = privacy_mapper.sanitize_for_model(ev.local_fact)
                sanitizer = getattr(privacy_mapper, "sanitize_text", None)
                if sanitizer and ev.display_summary:
                    item["summary"] = sanitizer(ev.display_summary)
                else:
                    item["summary"] = None
            result.append(item)
        return result

    def for_display(self, privacy_mapper=None, student_resolver=None) -> list[dict[str, Any]]:
        """序列化给教师界面（本地恢复姓名）。"""
        result = []
        for ev in self._evidence.values():
            item = {
                "evidence_id": ev.evidence_id,
                "evidence_type": ev.evidence_type,
                "fact": ev.local_fact,
                "summary": ev.display_summary,
                "source_entity": ev.source_entity,
                "source_field": ev.source_field,
                "source_page": ev.source_page,
                "source_question_no": ev.source_question_no,
                "calculation_formula": ev.calculation_formula,
                "numerator": ev.numerator,
                "denominator": ev.denominator,
                "rule_id": ev.rule_id,
                "rule_version": ev.rule_version,
            }
            if privacy_mapper and ev.contains_personal_data:
                item["fact"] = privacy_mapper.restore_for_display(
                    ev.local_fact, student_resolver
                )
            result.append(item)
        return result

    def validate_references(self, evidence_ids: list[str]) -> bool:
        """校验引用的证据 ID 是否全部存在。用于输出忠实度校验。"""
        return all(self.exists(eid) for eid in evidence_ids)


# ---------------------------------------------------------------------------
# 报告证据归属校验（B2-06：统一契约，工具层与 OutputValidator 共用）
# ---------------------------------------------------------------------------

# 报告允许引用的证据类型白名单
REPORT_ALLOWED_EVIDENCE_TYPES = {
    "db_metric",
    "computed_metric",
    "formal_document",
    "file_page",
    "question",
    "student_trend",
    "rule_signal",
    # RAG v3：教学依据（课标要求 / 题型诊断映射 / 错因干预规则），
    # 与成绩类证据一样可追溯到 knowledge 条目 ID 与可信度。
    "teaching_reference",
    # 记忆板块：教师确认的试卷记忆（本场考试的 AI 理解摘要）。
    "paper_memory",
}


def normalize_evidence_refs(refs: list[str] | None) -> list[str]:
    """规范化证据引用：去重且保持原有展示顺序。"""
    seen: set[str] = set()
    normalized: list[str] = []
    for ref in refs or []:
        if not ref:
            continue
        if ref not in seen:
            seen.add(ref)
            normalized.append(ref)
    return normalized


def validate_report_evidence_references(
    findings: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    *,
    ledger: EvidenceLedger | None = None,
    db_session: Any | None = None,
    run_id: int | None = None,
    allowed_types: set[str] | None = None,
) -> list[str]:
    """以同一份契约校验报告的 evidence_ids / supports 引用（B2-06）。

    每条引用必须同时满足：
      1. 非空（每个 finding 至少一个 evidence_ids、每个 recommendation 至少一个 supports）；
      2. 存在：命中内存 EvidenceLedger，或属于 ``run_id`` 的数据库持久化证据；
      3. 归属：不得引用其他运行（另一 run_id）的证据记录；
      4. 类型允许：evidence_type 在 ``allowed_types`` 白名单内。

    重复 ID 规范化去重，但不会改变展示顺序（不重写输入列表）。

    :param findings: 结构化答案的 findings 列表
    :param recommendations: 结构化答案的 recommendations 列表
    :param ledger: 当前运行的内存证据账本（可为 None）
    :param db_session: 数据库会话（提供时执行持久化证据与跨运行校验）
    :param run_id: 当前运行 ID（与 db_session 一起使用）
    :param allowed_types: 允许引用的证据类型白名单（默认 REPORT_ALLOWED_EVIDENCE_TYPES）
    :return: 错误列表；空列表表示通过
    """
    allowed = frozenset(allowed_types) if allowed_types else frozenset(REPORT_ALLOWED_EVIDENCE_TYPES)
    errors: list[str] = []

    own_map: dict[str, Any] | None = None
    if db_session is not None and run_id is not None:
        try:
            from ..models.agent_entities import AnalysisEvidence
            from sqlalchemy import select
            rows = db_session.scalars(
                select(AnalysisEvidence).where(AnalysisEvidence.run_id == run_id)
            ).all()
            own_map = {row.evidence_id: row for row in rows}
        except Exception:
            # DB 不可用时退化为仅内存校验
            own_map = None

    def _check_refs(refs: list[str], where: str) -> None:
        ledger_run_id = getattr(ledger, "_run_id", None) if ledger is not None else None
        for eid in refs:
            if ledger is not None:
                ev = ledger.get(eid)
                if ev is not None:
                    # 所有权校验（B2-02 审查修复）：内存账本归属必须与当前运行一致。
                    # 双方 ID 均已知且不一致 → 拒绝（防止用他 run 的 Ledger 蒙混过关）。
                    if (
                        run_id is not None
                        and ledger_run_id is not None
                        and ledger_run_id != run_id
                    ):
                        errors.append(
                            f"{where} 引用了其他运行的证据 ID: {eid}"
                            f"（内存账本归属 run {ledger_run_id}，当前 run {run_id}）"
                        )
                        continue
                    if ev.evidence_type not in allowed:
                        errors.append(
                            f"{where} 引用证据 {eid} 的类型 {ev.evidence_type} 不允许"
                        )
                    continue
            if own_map is not None and eid in own_map:
                row = own_map[eid]
                if row.evidence_type not in allowed:
                    errors.append(
                        f"{where} 引用证据 {eid} 的类型 {row.evidence_type} 不在允许范围"
                    )
                continue
            # 内存与当前 run 的 DB 均未命中：检查是否引用其他运行的证据
            if db_session is not None and run_id is not None:
                from ..models.agent_entities import AnalysisEvidence
                from sqlalchemy import select
                other_run = db_session.scalar(
                    select(AnalysisEvidence.id).where(
                        AnalysisEvidence.evidence_id == eid,
                        AnalysisEvidence.run_id != run_id,
                    ).limit(1)
                )
                if other_run is not None:
                    errors.append(f"{where} 引用了其他运行的证据 ID: {eid}")
                    continue
            errors.append(f"{where} 引用了不存在的证据 ID: {eid}")

    for index, finding in enumerate(findings or []):
        refs = normalize_evidence_refs(finding.get("evidence_ids") or [])
        where = f"finding #{index + 1}"
        if not refs:
            errors.append(f"{where} 缺少证据引用 (evidence_ids)")
            continue
        _check_refs(refs, where)

    for index, rec in enumerate(recommendations or []):
        # 契约要求 supports；同时宽容接受 evidence_ids（与 findings 同名的常见写法），
        # 兜底引用仍走存在性/归属/类型白名单校验，不放松安全。
        refs = normalize_evidence_refs(
            rec.get("supports") or rec.get("evidence_ids") or []
        )
        where = f"recommendation #{index + 1}"
        if not refs:
            errors.append(f"{where} 缺少证据引用 (supports)")
            continue
        _check_refs(refs, where)

    return errors
