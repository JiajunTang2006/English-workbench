"""证据持久化服务。

将 EvidenceLedger 中的证据写入 AnalysisEvidence 表，
支持从数据库恢复证据到内存。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.agent_entities import AnalysisEvidence

logger = logging.getLogger(__name__)


class EvidenceService:
    """证据持久化服务。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def save_evidence(
        self,
        *,
        run_id: int,
        evidence_id: str,
        evidence_type: str,
        local_fact: dict[str, Any],
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
    ) -> AnalysisEvidence:
        """持久化单条证据到数据库。"""
        evidence = AnalysisEvidence(
            run_id=run_id,
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            local_fact_json=local_fact,
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
        self._db.add(evidence)
        self._db.commit()
        self._db.refresh(evidence)
        return evidence

    def get_evidence_by_id(self, evidence_id: str) -> AnalysisEvidence | None:
        """通过 evidence_id 获取证据。"""
        return self._db.scalar(
            select(AnalysisEvidence).where(AnalysisEvidence.evidence_id == evidence_id)
        )

    def list_evidence_for_run(self, run_id: int) -> list[AnalysisEvidence]:
        """列出某次运行的全部证据。"""
        stmt = (
            select(AnalysisEvidence)
            .where(AnalysisEvidence.run_id == run_id)
            .order_by(AnalysisEvidence.created_at)
        )
        return list(self._db.scalars(stmt))

    def restore_to_ledger(self, run_id: int, ledger: Any) -> list[str]:
        """从数据库恢复证据到内存 EvidenceLedger。

        :return: 恢复的证据 ID 列表
        """
        evidence_list = self.list_evidence_for_run(run_id)
        restored_ids: list[str] = []
        for ev in evidence_list:
            eid = ledger.add(
                evidence_type=ev.evidence_type,
                local_fact=ev.local_fact_json,
                source_entity=ev.source_entity,
                source_field=ev.source_field,
                source_file=ev.source_file,
                source_page=ev.source_page,
                source_question_no=ev.source_question_no,
                source_cell=ev.source_cell,
                calculation_formula=ev.calculation_formula,
                numerator=ev.numerator,
                denominator=ev.denominator,
                rule_id=ev.rule_id,
                rule_version=ev.rule_version,
                contains_personal_data=ev.contains_personal_data,
                display_summary=ev.display_summary,
            )
            restored_ids.append(eid)
        logger.info("从运行 %d 恢复 %d 条证据", run_id, len(restored_ids))
        return restored_ids

    def save_ledger_to_db(self, run_id: int, ledger: Any) -> int:
        """将内存 EvidenceLedger 中的全部证据持久化到数据库。

        :return: 保存的证据数量
        """
        count = 0
        for ev in ledger._evidence.values():  # EvidenceLedger 内部存储
            self.save_evidence(
                run_id=run_id,
                evidence_id=ev.evidence_id,
                evidence_type=ev.evidence_type,
                local_fact=ev.local_fact,
                source_entity=ev.source_entity,
                source_field=ev.source_field,
                source_file=ev.source_file,
                source_page=ev.source_page,
                source_question_no=ev.source_question_no,
                source_cell=ev.source_cell,
                calculation_formula=ev.calculation_formula,
                numerator=ev.numerator,
                denominator=ev.denominator,
                rule_id=ev.rule_id,
                rule_version=ev.rule_version,
                contains_personal_data=ev.contains_personal_data,
                display_summary=ev.display_summary,
            )
            count += 1
        logger.info("持久化 %d 条证据到运行 %d", count, run_id)
        return count
