"""One source for a student's exam score details across UI and Agent tools."""

from __future__ import annotations

from sqlalchemy import func, select

from ..models.agent_entities import ExamQuestion, StudentItemResult
from ..models.entities import Exam, ExamScore, Student
from .paper_versions import select_paper_version


def get_student_score_details(db, *, exam_id: int, student_id: int,
                              class_id: int | None = None,
                              allow_draft: bool = False) -> dict:
    score = db.scalar(select(ExamScore).where(
        ExamScore.exam_id == exam_id, ExamScore.student_id == student_id,
    ))
    if score is None or (class_id is not None and score.class_id_at_exam != class_id):
        raise LookupError("当前范围内没有该学生的考试成绩")
    exam = db.get(Exam, exam_id)
    student = db.get(Student, student_id)
    if exam is None or student is None:
        raise LookupError("考试或学生不存在")

    # All consumers choose the same paper: the latest confirmed version wins.
    # 正式诊断默认只看 confirmed；审核界面可显式 allow_draft=True 请求草稿，
    # 并必须同时展示 paper_status，避免草稿被当成正式事实。
    version, paper_status = select_paper_version(db, exam_id, allow_draft=allow_draft)
    items: list[dict] = []
    sections: dict[str, dict] = {}
    if version is not None:
        rows = db.execute(
            select(ExamQuestion, StudentItemResult)
            .outerjoin(StudentItemResult,
                       (StudentItemResult.question_id == ExamQuestion.id)
                       & (StudentItemResult.student_id == student_id)
                       & (StudentItemResult.exam_id == exam_id))
            .where(ExamQuestion.paper_version_id == version.id)
            .order_by(ExamQuestion.id)
        ).all()
        for question, result in rows:
            section_name = question.section_name or question.question_type or "其他"
            value = result.score if result is not None else None
            source = ("teacher_override" if result and result.teacher_override else
                      "school_sync" if result and result.source_sync_run_id else
                      "attachment" if result and result.source_attachment_id else
                      "manual" if result else "missing")
            items.append({
                "question_no": question.question_no,
                "sub_question_no": question.sub_question_no,
                "section_name": section_name,
                "question_type": question.question_type,
                "score": value,
                "max_score": question.max_score,
                "full_score_known": question.max_score > 0,
                "status": "scored" if value is not None else "missing",
                "source": source,
            })
            section = sections.setdefault(section_name, {
                "section_name": section_name, "score": 0.0,
                "max_score": 0.0, "expected_items": 0, "scored_items": 0,
                "full_score_known": True,
            })
            section["max_score"] += question.max_score
            section["full_score_known"] = section["full_score_known"] and question.max_score > 0
            section["expected_items"] += 1
            if value is not None:
                section["score"] += value
                section["scored_items"] += 1

    scored_count = sum(item["score"] is not None for item in items)
    scored_total = round(sum(float(item["score"]) for item in items
                             if item["score"] is not None), 2)
    reconciliation = "unavailable"
    if items and scored_count == len(items) and score.total_score is not None:
        reconciliation = ("match" if abs(scored_total - score.total_score) <= 0.01
                          else "mismatch")
    class_rank = score.class_rank
    if class_rank is None and score.total_score is not None and score.class_id_at_exam:
        higher = db.scalar(select(func.count()).select_from(ExamScore).where(
            ExamScore.exam_id == exam_id,
            ExamScore.class_id_at_exam == score.class_id_at_exam,
            ExamScore.total_score > score.total_score,
        )) or 0
        class_rank = higher + 1
    return {
        "exam_id": exam_id,
        "student_id": student_id,
        "student_name": student.name,
        "exam_name": exam.name,
        "total_score": score.total_score,
        "class_rank": class_rank,
        "attendance": score.attendance_status,
        "paper_version": version.version if version else None,
        "paper_status": paper_status,
        "paper_structure_hash": version.structure_hash if version else None,
        "score_updated_at": score.updated_at.isoformat() if score.updated_at else None,
        "expected_items": len(items),
        "scored_items": scored_count,
        "scored_total": scored_total if scored_count else None,
        "total_reconciliation": reconciliation,
        "detail_status": ("no_paper" if version is None else
                          "missing" if not scored_count else
                          "complete" if scored_count == len(items) else "partial"),
        "section_scores": list(sections.values()),
        "item_scores": items,
    }
