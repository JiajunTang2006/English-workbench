"""学期级 TeachMate 缓存清理。

缓存只包含 AI 生成的中间产物和对话上下文，不包含成绩、考试结构、正式附件
或教师确认后的长期资料。所有清理都按 term_id 限定，并由路由层要求教师确认。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import (
    AgentAnalysisSetting,
    AgentMessage,
    AgentMessageAttachment,
    AgentSession,
    AnalysisEvidence,
    AnalysisRun,
    AnalysisRunEvent,
    ErrorCauseAssessment,
    Exam,
    ExamPaperMemory,
    LlmUsageRecord,
    StudentEvaluation,
    StudentProfileRevision,
    Term,
)


_ACTIVE_RUN_STATUSES = {"created", "validating", "estimating", "waiting_confirmation", "queued", "running"}
_CACHE_REVISION_STATUSES = {"draft"}
_CACHE_ERROR_CAUSE_STATUSES = {"candidate", "rejected"}


def _term_exists(db: Session, term_id: int) -> Term:
    term = db.get(Term, term_id)
    if term is None:
        raise ValueError("学期不存在")
    return term


def _ids_for_term(db: Session, term_id: int) -> tuple[list[int], list[int]]:
    exam_ids = list(db.scalars(select(Exam.id).where(Exam.term_id == term_id)))
    session_ids = list(db.scalars(select(AgentSession.id).where(AgentSession.term_id == term_id)))
    return exam_ids, session_ids


def cache_summary(db: Session, term_id: int) -> dict[str, Any]:
    """返回清理前预览，不读取消息正文。"""
    _term_exists(db, term_id)
    exam_ids, session_ids = _ids_for_term(db, term_id)
    run_ids = list(db.scalars(select(AnalysisRun.id).where(AnalysisRun.term_id == term_id)))
    active_runs = int(db.scalar(
        select(func.count(AnalysisRun.id)).where(
            AnalysisRun.term_id == term_id,
            AnalysisRun.status.in_(_ACTIVE_RUN_STATUSES),
        )
    ) or 0)
    session_id_filter = AgentMessage.session_id.in_(session_ids) if session_ids else False
    run_id_filter = AnalysisEvidence.run_id.in_(run_ids) if run_ids else False
    message_link_ids = list(db.scalars(
        select(AgentMessageAttachment.id)
        .join(AgentMessage, AgentMessageAttachment.message_id == AgentMessage.id)
        .where(session_id_filter)
    )) if session_ids else []
    return {
        "term_id": term_id,
        "exam_paper_memories": int(db.scalar(
            select(func.count(ExamPaperMemory.id)).where(
                ExamPaperMemory.exam_id.in_(exam_ids) if exam_ids else False
            )
        ) or 0),
        "sessions": len(session_ids),
        "messages": int(db.scalar(
            select(func.count(AgentMessage.id)).where(session_id_filter)
        ) or 0) if session_ids else 0,
        "message_attachments": len(message_link_ids),
        "analysis_runs": len(run_ids),
        "analysis_evidence": int(db.scalar(
            select(func.count(AnalysisEvidence.id)).where(run_id_filter)
        ) or 0) if run_ids else 0,
        "analysis_events": int(db.scalar(
            select(func.count(AnalysisRunEvent.id)).where(
                AnalysisRunEvent.run_id.in_(run_ids) if run_ids else False
            )
        ) or 0) if run_ids else 0,
        "llm_usage_records": int(db.scalar(
            select(func.count(LlmUsageRecord.id)).where(
                LlmUsageRecord.run_id.in_(run_ids) if run_ids else False
            )
        ) or 0) if run_ids else 0,
        "student_profile_drafts": int(db.scalar(
            select(func.count(StudentProfileRevision.id)).where(
                StudentProfileRevision.term_id == term_id,
                StudentProfileRevision.status.in_(_CACHE_REVISION_STATUSES),
            )
        ) or 0),
        "evaluation_drafts": int(db.scalar(
            select(func.count(StudentEvaluation.id)).where(
                StudentEvaluation.term_id == term_id,
                StudentEvaluation.status == "draft",
            )
        ) or 0),
        "error_cause_candidates": int(db.scalar(
            select(func.count(ErrorCauseAssessment.id)).where(
                ErrorCauseAssessment.exam_id.in_(exam_ids) if exam_ids else False,
                ErrorCauseAssessment.status.in_(_CACHE_ERROR_CAUSE_STATUSES),
            )
        ) or 0) if exam_ids else 0,
        "active_runs": active_runs,
        "static_knowledge_files_preserved": True,
        "formal_attachments_preserved": True,
        "teacher_confirmed_profiles_and_evaluations_preserved": True,
    }


def clear_cache(db: Session, term_id: int, *, data_dir: Path | None = None) -> dict[str, Any]:
    """清理指定学期的 AI 缓存并返回实际删除数量。

    运行中的任务会被拒绝清理，避免后台任务在清理过程中继续写入数据。
    """
    summary = cache_summary(db, term_id)
    if summary["active_runs"]:
        raise RuntimeError("该学期仍有正在运行的分析，请等待完成或取消后再清理")
    exam_ids, session_ids = _ids_for_term(db, term_id)
    run_ids = list(db.scalars(select(AnalysisRun.id).where(AnalysisRun.term_id == term_id)))

    # 收集未晋升为正式资料的临时聊天文件；正式附件一律不碰。
    chat_paths = []
    if session_ids:
        links = list(db.scalars(
            select(AgentMessageAttachment)
            .join(AgentMessage, AgentMessageAttachment.message_id == AgentMessage.id)
            .where(AgentMessage.session_id.in_(session_ids))
        ))
        chat_paths = [link.chat_attachment_path for link in links
                      if link.chat_attachment_path and not link.promoted_to_formal]

    # 先解除仍被保留的教师确认记录对运行记录的引用，再删除运行缓存。
    if run_ids:
        db.query(StudentEvaluation).filter(StudentEvaluation.analysis_run_id.in_(run_ids)).update(
            {StudentEvaluation.analysis_run_id: None}, synchronize_session=False
        )
        db.query(ErrorCauseAssessment).filter(ErrorCauseAssessment.analysis_run_id.in_(run_ids)).update(
            {ErrorCauseAssessment.analysis_run_id: None}, synchronize_session=False
        )
        db.execute(delete(AnalysisRunEvent).where(AnalysisRunEvent.run_id.in_(run_ids)))
        db.execute(delete(AnalysisEvidence).where(AnalysisEvidence.run_id.in_(run_ids)))
        db.execute(delete(LlmUsageRecord).where(LlmUsageRecord.run_id.in_(run_ids)))
        db.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))

    if exam_ids:
        db.execute(delete(ExamPaperMemory).where(ExamPaperMemory.exam_id.in_(exam_ids)))
        db.execute(delete(ErrorCauseAssessment).where(
            ErrorCauseAssessment.exam_id.in_(exam_ids),
            ErrorCauseAssessment.status.in_(_CACHE_ERROR_CAUSE_STATUSES),
        ))

    if session_ids:
        db.execute(delete(AgentMessageAttachment).where(
            AgentMessageAttachment.message_id.in_(
                select(AgentMessage.id).where(AgentMessage.session_id.in_(session_ids))
            )
        ))
        db.execute(delete(AgentMessage).where(AgentMessage.session_id.in_(session_ids)))
        db.execute(delete(AgentSession).where(AgentSession.id.in_(session_ids)))

    db.execute(delete(StudentProfileRevision).where(
        StudentProfileRevision.term_id == term_id,
        StudentProfileRevision.status.in_(_CACHE_REVISION_STATUSES),
    ))
    db.execute(delete(StudentEvaluation).where(
        StudentEvaluation.term_id == term_id,
        StudentEvaluation.status == "draft",
    ))
    db.execute(delete(AgentAnalysisSetting).where(
        AgentAnalysisSetting.key == f"teachmate_session_preferences:{term_id}"
    ))
    db.commit()

    removed_files = 0
    if data_dir:
        root = Path(data_dir).resolve()
        for raw in chat_paths:
            try:
                candidate = Path(raw)
                if not candidate.is_absolute():
                    candidate = root / candidate
                candidate = candidate.resolve()
                if candidate.is_file() and candidate.is_relative_to(root):
                    candidate.unlink()
                    removed_files += 1
            except (OSError, RuntimeError, ValueError):
                # 文件清理失败不回滚已完成的数据库清理；避免越界路径影响接口。
                continue

    return {
        "term_id": term_id,
        "removed": {
            key: value for key, value in summary.items()
            if key not in {"term_id", "active_runs", "static_knowledge_files_preserved",
                           "formal_attachments_preserved",
                           "teacher_confirmed_profiles_and_evaluations_preserved"}
        },
        "temporary_chat_files_removed": removed_files,
        "static_knowledge_files_preserved": True,
        "formal_attachments_preserved": True,
        "teacher_confirmed_profiles_and_evaluations_preserved": True,
    }
