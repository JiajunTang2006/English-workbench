"""运行持久化仓库 (U2-01)

负责所有与 analysis_runs 相关的数据库操作。
将持久化逻辑从 run_executor 中拆出，使 run_executor 专注于编排。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.agent_entities import (
    AgentMessage,
    AnalysisEvidence,
    AnalysisRun,
    LlmUsageRecord,
)

logger = logging.getLogger(__name__)


class RunRepository:
    """AnalysisRun 持久化仓库。"""

    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_run(self, run_id: int) -> Optional[AnalysisRun]:
        return self._db.scalar(
            select(AnalysisRun).where(AnalysisRun.id == run_id)
        )

    def get_run_status(self, run_id: int) -> Optional[str]:
        run = self.get_run(run_id)
        return run.status if run else None

    # ------------------------------------------------------------------
    # 状态转换（唯一入口）
    # ------------------------------------------------------------------

    # 合法的状态转换
    _VALID_TRANSITIONS: dict[str, frozenset[str]] = {
        "created": frozenset({
            "validating", "cancelled", "failed", "interrupted",
        }),
        "validating": frozenset({
            "estimating", "failed", "cancelled", "interrupted",
        }),
        "estimating": frozenset({
            "waiting_confirmation", "queued", "failed", "cancelled", "interrupted",
        }),
        "waiting_confirmation": frozenset({
            "queued", "cancelled", "interrupted",
        }),
        "queued": frozenset({
            "running", "completed", "degraded", "failed", "cancelled", "interrupted",
        }),
        "running": frozenset({
            "validating_output", "completed", "degraded", "failed",
            "cancelled", "waiting_confirmation", "interrupted", "queued",
        }),
        "validating_output": frozenset({
            "completed", "degraded", "failed", "cancelled", "interrupted",
        }),
        # interrupted 是非终态恢复态：可转为 queued（重试）、failed、cancelled
        "interrupted": frozenset({
            "queued", "failed", "cancelled",
        }),
        # 终态不可转出
        "completed": frozenset(),
        "degraded": frozenset(),
        "failed": frozenset(),
        "cancelled": frozenset(),
    }

    def transition_status(
        self, run_id: int, new_status: str
    ) -> AnalysisRun:
        """
        执行状态转换并校验合法性。
        如果转换不合法则抛出 ValueError。
        """
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")

        old_status = run.status
        if old_status == new_status:
            return run

        allowed = self._VALID_TRANSITIONS.get(old_status, frozenset())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid status transition: {old_status} -> {new_status}"
                f" (allowed: {allowed})"
            )

        run.status = new_status
        if new_status in ("completed", "degraded", "failed", "cancelled", "interrupted"):
            run.completed_at = datetime.now(timezone.utc)
        elif new_status == "running" and not run.started_at:
            run.started_at = datetime.now(timezone.utc)

        self._db.flush()
        return run

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def update_run_fields(
        self, run_id: int, **fields: Any
    ) -> Optional[AnalysisRun]:
        """直接更新 run 字段。"""
        run = self.get_run(run_id)
        if run is None:
            return None
        for key, value in fields.items():
            if hasattr(run, key):
                setattr(run, key, value)
        self._db.flush()
        return run

    def set_runtime_metadata(
        self,
        run_id: int,
        *,
        runtime_kind: Optional[str] = None,
        runtime_version: Optional[str] = None,
        harness_session_id: Optional[str] = None,
        harness_message_id: Optional[str] = None,
        runtime_generation: Optional[int] = None,
        config_version: Optional[int] = None,
        rules_version: Optional[str] = None,
    ) -> Optional[AnalysisRun]:
        """设置运行时元数据。"""
        fields: dict[str, Any] = {}
        if runtime_kind is not None:
            fields["runtime_kind"] = runtime_kind
        if runtime_version is not None:
            fields["runtime_version"] = runtime_version
        if harness_session_id is not None:
            fields["harness_session_id"] = harness_session_id
        if harness_message_id is not None:
            fields["harness_message_id"] = harness_message_id
        if runtime_generation is not None:
            fields["runtime_generation"] = runtime_generation
        if config_version is not None:
            fields["config_version"] = config_version
        if rules_version is not None:
            fields["rules_version"] = rules_version
        return self.update_run_fields(run_id, **fields)

    def save_evidence(
        self, run_id: int, evidence: list[dict]
    ) -> None:
        """持久化证据。"""
        for ev in evidence:
            evidence_row = AnalysisEvidence(
                evidence_id=ev.get("evidence_id", ev.get("evidenceId", "")),
                run_id=run_id,
                evidence_type=ev.get("evidence_type", "db_metric"),
                local_fact_json=ev.get("local_fact", ev.get("fact", {})),
                source_entity=ev.get("source_entity"),
                display_summary=ev.get("display_summary"),
                contains_personal_data=ev.get("contains_personal_data", False),
            )
            self._db.add(evidence_row)
        self._db.flush()

    def save_llm_usage(
        self,
        run_id: int,
        usage_records: list[dict],
    ) -> None:
        """持久化 LLM 用量记录。"""
        for rec in usage_records:
            usage_row = LlmUsageRecord(
                run_id=run_id,
                provider=rec.get("provider", ""),
                model_name=rec.get("model_name", ""),
                stage=rec.get("stage", "text_analysis"),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cost_yuan=rec.get("cost_yuan", 0.0),
                provider_request_id=rec.get("provider_request_id"),
            )
            self._db.add(usage_row)
        self._db.flush()

    def save_assistant_message(
        self,
        session_id: int,
        run_id: int,
        *,
        content_text: str = "",
        structured_answer: Optional[dict] = None,
        evidence_ids: Optional[list[str]] = None,
        model_name: str = "",
        provider_request_id: Optional[str] = None,
    ) -> AgentMessage:
        """保存助手消息。"""
        msg = AgentMessage(
            session_id=session_id,
            analysis_run_id=run_id,
            role="assistant",
            content_text=content_text,
            structured_answer_json=structured_answer or {},
            evidence_ids_json=evidence_ids or [],
            model_name=model_name,
            provider_request_id=provider_request_id,
        )
        self._db.add(msg)
        self._db.flush()
        return msg

    def commit(self) -> None:
        self._db.commit()

    def flush(self) -> None:
        self._db.flush()
