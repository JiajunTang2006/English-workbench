"""分析报告的数据范围快照。

报告生成后，成绩和班级名称仍可能被教师修订。外部 Agent 读取历史报告时必须
同时拿到“生成当时”的范围和数据质量，不能把旧结论与当前数据库状态拼在一起。
快照存放在 AnalysisRun.input_summary_json 中，避免引入第二套报告存储。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from ...models.agent_entities import (
    AnalysisRun,
    ExamPaperVersion,
    ExamQuestion,
    QuestionKnowledgePoint,
    StudentItemResult,
)
from ...models.entities import Attachment, Class, Exam, ExamScore, Term


SNAPSHOT_KEY = "report_scope_snapshot"
SNAPSHOT_VERSION = "1.2.0"


def build_report_scope_snapshot(session, run: AnalysisRun) -> dict:
    """根据运行绑定的真实 ID 构建不含学生身份信息的确定性快照。"""
    term = session.get(Term, run.term_id)
    exam = session.get(Exam, run.exam_id) if run.exam_id is not None else None
    classroom = session.get(Class, run.class_id) if run.class_id is not None else None

    rows = []
    if exam is not None:
        stmt = select(ExamScore).where(ExamScore.exam_id == exam.id)
        if run.class_id is not None:
            stmt = stmt.where(ExamScore.class_id_at_exam == run.class_id)
        rows = session.scalars(stmt.order_by(ExamScore.id)).all()

    present = [row for row in rows if row.attendance_status == "present"]
    scored = [float(row.total_score) for row in present if row.total_score is not None]
    absent_count = len(rows) - len(present)
    missing_scores = max(0, len(present) - len(scored))
    score_facts = [
        [row.id, row.attendance_status, float(row.total_score) if row.total_score is not None else None]
        for row in rows
    ]
    paper_facts = None
    item_facts: list[list] = []
    if exam is not None:
        versions = session.scalars(select(ExamPaperVersion).where(
            ExamPaperVersion.exam_id == exam.id,
        ).order_by(ExamPaperVersion.version.desc())).all()
        version = next((item for item in versions if item.status == "confirmed"),
                       versions[0] if versions else None)
        if version is not None:
            questions = session.scalars(select(ExamQuestion).where(
                ExamQuestion.paper_version_id == version.id,
            ).order_by(ExamQuestion.id)).all()
            question_ids = [q.id for q in questions]
            # 知识点标签既可能落在题目的 JSON 字段，也可能落在关联表。只比对
            # 题目结构无法发现「改了知识点标签但指纹仍是 current」，因此两者
            # 都要纳入指纹，保证结论过期能被检测出来。
            knowledge_links: list[list] = []
            if question_ids:
                link_rows = session.scalars(select(QuestionKnowledgePoint).where(
                    QuestionKnowledgePoint.question_id.in_(question_ids),
                ).order_by(QuestionKnowledgePoint.id)).all()
                knowledge_links = [
                    [link.question_id, link.knowledge_point_id, bool(link.is_primary),
                     link.source, bool(link.confirmed_by_teacher)]
                    for link in link_rows
                ]
            paper_facts = [
                version.id, version.version, version.status, version.structure_hash,
                [[q.id, q.question_no, q.sub_question_no, q.max_score,
                  q.section_name, q.question_type,
                  q.knowledge_nodes_json, q.ability_nodes_json,
                  q.pitfall_tags_json, q.teaching_blocks_json,
                  bool(q.included_in_analysis), q.correct_answer_json]
                 for q in questions],
                knowledge_links,
            ]
            if questions and rows:
                item_rows = session.scalars(select(StudentItemResult).where(
                    StudentItemResult.exam_id == exam.id,
                    StudentItemResult.question_id.in_(question_ids),
                    StudentItemResult.student_id.in_([r.student_id for r in rows]),
                ).order_by(StudentItemResult.id)).all()
                item_facts = [
                    [item.student_id, item.question_id, item.score, item.score_rate,
                     item.correct, item.selected_option, item.student_answer_text,
                     bool(item.teacher_override)]
                    for item in item_rows
                ]

    # 正式资料版本：附件的 sha256 决定内容，纳入指纹后资料被替换/重传会
    # 使旧报告过期，而不是悄悄沿用旧结论。
    material_facts: list[list] = []
    summary_ids = (run.input_summary_json or {}).get("attachment_ids")
    if isinstance(summary_ids, list) and summary_ids:
        normalized = [int(value) for value in summary_ids
                      if isinstance(value, int) or str(value).isdigit()]
        if normalized:
            attachments = session.scalars(select(Attachment).where(
                Attachment.id.in_(normalized),
            ).order_by(Attachment.id)).all()
            material_facts = [[item.id, item.sha256, item.size_bytes]
                              for item in attachments]

    fingerprint_payload = {
        "exam": [exam.id, exam.name, exam.full_score] if exam else None,
        "scores": score_facts,
        "paper": paper_facts,
        "items": item_facts,
        "materials": material_facts,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]

    input_summary = run.input_summary_json or {}
    attachment_ids = input_summary.get("attachment_ids")
    material_count = len(attachment_ids) if isinstance(attachment_ids, list) else 0
    return {
        "version": SNAPSHOT_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "term_id": run.term_id,
            "term_name": term.name if term else str(run.term_id),
            "class_id": run.class_id,
            "class_name": classroom.name if classroom else None,
            "exam_id": run.exam_id,
            "exam_name": exam.name if exam else None,
            "full_score": float(exam.full_score) if exam else None,
        },
        "data_quality": {
            "present_count": len(present),
            "scored_count": len(scored),
            "absent_count": absent_count,
            "missing_scores": missing_scores,
        },
        "score_summary": {
            "average": round(sum(scored) / len(scored), 2) if scored else None,
            "highest": max(scored) if scored else None,
            "lowest": min(scored) if scored else None,
        },
        "source_summary": {
            "score_record_count": len(rows),
            "formal_material_count": material_count,
            "data_fingerprint": fingerprint,
        },
    }


def ensure_report_scope_snapshot(session, run: AnalysisRun) -> dict:
    """只创建一次快照；后续读取或重复提交不会重写历史事实。"""
    summary = dict(run.input_summary_json or {})
    existing = summary.get(SNAPSHOT_KEY)
    if isinstance(existing, dict) and existing.get("version"):
        return existing
    snapshot = build_report_scope_snapshot(session, run)
    summary[SNAPSHOT_KEY] = snapshot
    run.input_summary_json = summary
    session.flush()
    return snapshot


def read_report_scope_snapshot(session, run: AnalysisRun) -> tuple[dict, str]:
    """读取冻结快照；旧报告仅作兼容重建，并明确标记为非冻结来源。"""
    existing = (run.input_summary_json or {}).get(SNAPSHOT_KEY)
    if isinstance(existing, dict) and existing.get("version"):
        return existing, "frozen"
    return build_report_scope_snapshot(session, run), "legacy_reconstructed"


def report_freshness(session, run: AnalysisRun) -> str:
    """Compare the frozen facts with current exam data without rewriting history."""
    snapshot = (run.input_summary_json or {}).get(SNAPSHOT_KEY)
    if not isinstance(snapshot, dict) or snapshot.get("version") != SNAPSHOT_VERSION:
        return "unknown"
    if run.exam_id is None:
        return "unknown"  # Attachment-only reports need document revision tracking.
    previous = (snapshot.get("source_summary") or {}).get("data_fingerprint")
    current = build_report_scope_snapshot(session, run)["source_summary"]["data_fingerprint"]
    return "current" if previous == current else "stale"
