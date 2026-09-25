"""L4 文档导出编排服务（方案 §11）。

职责：
- 从已确认来源取材（session 最后一条结构化答案 + 该 run 的证据附录）；
- 选择本地 Provider（fail-closed，远端需显式启用）；
- 落盘到 {data_dir}/exports/，登记 GeneratedArtifact 元数据；
- 维护 DocumentExportJob 状态机（queued→running→completed/failed）；
- 幂等：相同 (源快照 + 模板版本 + 格式) 复用既有产物，避免重复计费/文件。

不污染依赖：本地 PDF 复用系统 weasyprint 探测，DOCX 零依赖手写 OOXML。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.agent_entities import AgentMessage, AnalysisEvidence
from ...models.entities import (
    DocumentExportJob,
    GeneratedArtifact,
)
from .provider import get_provider

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentExportService:
    def __init__(self, db: Session, data_dir: str | Path) -> None:
        self._db = db
        self._data_dir = Path(data_dir)
        self._exports_dir = self._data_dir / "exports"

    # ------------------------------------------------------------------
    # 取材（只取已确认内容，不渲染未确认模型输出）
    # ------------------------------------------------------------------
    def _resolve_answer_and_evidence(
        self, *, session_ids: list[int], inline_content: dict | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if inline_content:
            return inline_content, []
        answer: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        run_ids: set[int] = set()
        for sid in session_ids:
            stmt = (
                select(AgentMessage)
                .where(AgentMessage.session_id == sid)
                .order_by(AgentMessage.created_at.desc())
                .limit(20)
            )
            for msg in self._db.scalars(stmt):
                saj = msg.structured_answer_json
                if saj and saj.get("summary"):
                    # 取最新一条有结构化答案的消息作为确认稿
                    if not answer:
                        answer = dict(saj)
                # 该消息隶属的运行（用于收集证据附录）
                if msg.analysis_run_id is not None:
                    run_ids.add(msg.analysis_run_id)
        # 证据附录：取这些 run 的证据
        if run_ids:
            estmt = select(AnalysisEvidence).where(AnalysisEvidence.run_id.in_(run_ids))
            for ev in self._db.scalars(estmt):
                evidence.append({
                    "evidence_id": ev.evidence_id,
                    "evidence_type": ev.evidence_type,
                    "display_summary": ev.display_summary,
                    "source_file": ev.source_file,
                    "source_page": ev.source_page,
                })
        if not answer:
            answer = {"summary": "", "findings": [], "recommendations": [], "limitations": []}
        return answer, evidence

    def _idempotency_key(self, *, spec: dict, template_version: str, fmt: str) -> str:
        payload = (
            f"{fmt}|{template_version}|"
            f"{sorted(spec.get('source', {}).get('session_ids', []))}|"
            f"{sorted(spec.get('source', {}).get('attachment_ids', []))}|"
            f"{spec.get('title', '')}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:32]

    # ------------------------------------------------------------------
    # 导出主流程
    # ------------------------------------------------------------------
    async def export_document(
        self,
        *,
        spec: dict[str, Any],
        template_version: str = "1.0",
        run_id: int | None = None,
        session_id: int | None = None,
        privacy_level: str = "confidential",
    ) -> dict[str, Any]:
        fmt = spec["format"]
        source = spec.get("source", {})
        inline = spec.get("inline_content")
        session_ids = source.get("session_ids", []) or ([session_id] if session_id else [])

        answer, evidence = self._resolve_answer_and_evidence(
            session_ids=session_ids, inline_content=inline
        )
        meta = spec.get("options", {}).get("meta") or {}

        idek = self._idempotency_key(spec=spec, template_version=template_version, fmt=fmt)

        # 幂等：检查既有已完成 job
        existing = self._db.scalars(
            select(DocumentExportJob).where(
                DocumentExportJob.status == "completed",
                DocumentExportJob.format == fmt,
                DocumentExportJob.template_version == template_version,
            )
        ).all()
        for job in existing:
            snap = job.input_snapshot_json or {}
            if snap.get("idempotency_key") == idek and job.output_artifact_id:
                artifact = self._db.get(GeneratedArtifact, job.output_artifact_id)
                if artifact is not None:
                    return self._job_view(job, artifact, reused=True)

        job = DocumentExportJob(
            run_id=run_id,
            session_id=session_id if session_id else (session_ids[0] if session_ids else None),
            format=fmt,
            provider="local",
            template_id=spec.get("template", "report"),
            template_version=template_version,
            status="running",
            input_snapshot_json={"idempotency_key": idek, "spec": spec},
        )
        self._db.add(job)
        self._db.commit()
        self._db.refresh(job)

        try:
            provider = get_provider(fmt)
            result = await provider.submit(
                spec=spec, title=spec.get("title", ""),
                answer=answer, evidence=evidence, meta=meta,
                target_dir=self._exports_dir, idempotency_key=idek,
                privacy_level=privacy_level,
            )
            artifact = GeneratedArtifact(
                kind="document" if result.handle.produced_format != "html" else "print_html",
                storage_name=result.handle.artifact_path.name,
                mime_type=result.handle.mime_type,
                size_bytes=result.size_bytes,
                sha256=result.sha256,
                contains_personal_data=True,  # 教学报告默认含学生数据
            )
            self._db.add(artifact)
            self._db.commit()
            self._db.refresh(artifact)

            job.status = "completed"
            job.output_artifact_id = artifact.id
            job.completed_at = _utcnow()
            job.input_snapshot_json = {
                **(job.input_snapshot_json or {}),
                "produced_format": result.handle.produced_format,
                "format_hint": result.handle.format_hint,
            }
            self._db.commit()
            return self._job_view(job, artifact, reused=False)
        except Exception as e:  # 失败不丢失报告内容（内容已在 answer 中）
            self._db.rollback()
            job = self._db.get(DocumentExportJob, job.id)
            if job is not None:
                job.status = "failed"
                job.error_message = str(e)[:2000]
                job.completed_at = _utcnow()
                self._db.commit()
            logger.exception("L4 文档导出失败 job=%s", job.id if job else None)
            raise

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_job(self, job_id: int) -> dict[str, Any] | None:
        job = self._db.get(DocumentExportJob, job_id)
        if job is None:
            return None
        artifact = (
            self._db.get(GeneratedArtifact, job.output_artifact_id)
            if job.output_artifact_id else None
        )
        return self._job_view(job, artifact, reused=False)

    def _job_view(self, job: DocumentExportJob, artifact: GeneratedArtifact | None,
                  *, reused: bool) -> dict[str, Any]:
        snap = job.input_snapshot_json or {}
        return {
            "job_id": job.id,
            "status": job.status,
            "format": job.format,
            "produced_format": snap.get("produced_format", job.format),
            "format_hint": snap.get("format_hint", ""),
            "reused": reused,
            "artifact": (
                {
                    "id": artifact.id,
                    "storage_name": artifact.storage_name,
                    "mime_type": artifact.mime_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "contains_personal_data": artifact.contains_personal_data,
                }
                if artifact else None
            ),
            "error_message": job.error_message,
        }
