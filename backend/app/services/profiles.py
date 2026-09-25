from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Class, Enrollment, Exam, ExamQuestion, ExamScore, Student, StudentItemResult
from ..models.agent_entities import AgentMessage, AgentSession, AnalysisRun
from ..schemas.exams import StudentExamPoint, StudentProfileRead
from .exams import score_rows
from .student_profiles import apply_analysis_report_to_profile, get_profile_payload


QUESTION_TYPE_CATEGORIES = (
    "听力理解",
    "阅读理解",
    "完形填空",
    "词汇运用",
    "语法填空",
    "任务型阅读",
    "书面表达",
)


def _question_type_category(section_name: str | None, question_type: str | None) -> str | None:
    """将 MONI 的卷面字段归并到命题解析中的七类题型。"""
    text = f"{section_name or ''} {question_type or ''}".strip()
    if not text:
        return None
    if "任务型阅读" in text:
        return "任务型阅读"
    if "听力" in text or "人机对话" in text:
        return "听力理解"
    if "完形" in text:
        return "完形填空"
    if "阅读" in text:
        return "阅读理解"
    if any(mark in text for mark in ("语法", "grammar")):
        return "语法填空"
    if any(mark in text for mark in ("词汇", "选词", "单词拼写", "词形", "vocabulary")):
        return "词汇运用"
    if any(mark in text for mark in ("作文", "书面表达", "写作", "writing")):
        return "书面表达"
    return None


def question_type_tracking(
    session: Session,
    student_id: int,
    *,
    term_id: int,
    scores: list[ExamScore],
) -> dict:
    """Return per-exam question-type rates and their longitudinal averages.

    Each exam contributes one rate per type (type score / type full score), and
    the radar uses the arithmetic mean of those exam-level rates. Missing item
    scores are excluded rather than treated as zero.
    """
    present_scores = [
        item for item in scores
        if item.attendance_status == "present" and item.total_score is not None
    ]
    exam_ids = [item.exam_id for item in present_scores]
    empty = {
        "categories": list(QUESTION_TYPE_CATEGORIES),
        "averages": [{"type": name, "score_rate": None, "exam_count": 0, "item_count": 0} for name in QUESTION_TYPE_CATEGORIES],
        "history": [],
        "exam_count": 0,
        "data_source": "暂无小题成绩",
    }
    if not exam_ids:
        return empty

    rows = session.execute(
        select(
            Exam.id,
            Exam.name,
            Exam.exam_date,
            ExamQuestion.section_name,
            ExamQuestion.question_type,
            ExamQuestion.max_score,
            StudentItemResult.score,
            StudentItemResult.source_sync_run_id,
        )
        .join(StudentItemResult, StudentItemResult.exam_id == Exam.id)
        .join(ExamQuestion, ExamQuestion.id == StudentItemResult.question_id)
        .where(
            Exam.term_id == term_id,
            Exam.status == "active",
            Exam.id.in_(exam_ids),
            StudentItemResult.student_id == student_id,
            StudentItemResult.attendance_status == "present",
        )
    ).all()

    grouped: dict[int, dict[str, dict[str, float | int]]] = {}
    exam_meta: dict[int, dict[str, object]] = {}
    has_moni_rows = False
    for exam_id, exam_name, exam_date, section_name, question_type, max_score, score, source_sync_run_id in rows:
        category = _question_type_category(section_name, question_type)
        if not category or score is None or max_score is None or max_score <= 0:
            continue
        exam_meta[exam_id] = {"exam_name": exam_name, "exam_date": exam_date}
        bucket = grouped.setdefault(exam_id, {}).setdefault(category, {"score": 0.0, "max_score": 0.0, "item_count": 0})
        bucket["score"] += float(score)
        bucket["max_score"] += float(max_score)
        bucket["item_count"] += 1
        has_moni_rows = has_moni_rows or source_sync_run_id is not None

    history: list[dict] = []
    for score in sorted(
        present_scores,
        key=lambda item: (item.exam.exam_date.isoformat() if item.exam.exam_date else "", item.exam_id),
    ):
        buckets = grouped.get(score.exam_id, {})
        values = []
        for category in QUESTION_TYPE_CATEGORIES:
            bucket = buckets.get(category)
            if not bucket or not bucket["max_score"]:
                continue
            values.append({
                "type": category,
                "score": round(float(bucket["score"]), 2),
                "max_score": round(float(bucket["max_score"]), 2),
                "score_rate": round(float(bucket["score"]) / float(bucket["max_score"]) * 100, 2),
                "item_count": int(bucket["item_count"]),
            })
        if values:
            meta = exam_meta.get(score.exam_id, {"exam_name": score.exam.name, "exam_date": score.exam.exam_date})
            history.append({
                "exam_id": score.exam_id,
                "exam_name": meta["exam_name"],
                "exam_date": meta["exam_date"],
                "values": values,
            })

    rates: dict[str, list[float]] = {name: [] for name in QUESTION_TYPE_CATEGORIES}
    item_counts: dict[str, int] = {name: 0 for name in QUESTION_TYPE_CATEGORIES}
    for exam in history:
        for value in exam["values"]:
            rates[value["type"]].append(float(value["score_rate"]))
            item_counts[value["type"]] += int(value["item_count"])

    return {
        "categories": list(QUESTION_TYPE_CATEGORIES),
        "averages": [
            {
                "type": name,
                "score_rate": round(sum(rates[name]) / len(rates[name]), 2) if rates[name] else None,
                "exam_count": len(rates[name]),
                "item_count": item_counts[name],
            }
            for name in QUESTION_TYPE_CATEGORIES
        ],
        "history": history,
        "exam_count": len(history),
        "data_source": "MONI 小题成绩" if has_moni_rows else "已录入的小题成绩",
    }


def student_profile(session: Session, student_id: int, *, term_id: int) -> StudentProfileRead:
    student = session.scalar(
        select(Student).options(selectinload(Student.classroom)).where(Student.id == student_id)
    )
    enrollment = session.scalar(select(Enrollment).where(
        Enrollment.term_id == term_id,
        Enrollment.student_id == student_id,
        Enrollment.status == "active",
    ))
    if not student or not enrollment:
        raise HTTPException(404, "学生不存在")
    classroom = session.get(Class, enrollment.class_id)
    scores = list(session.scalars(
        select(ExamScore)
        .options(selectinload(ExamScore.exam))
        .join(Exam)
        .where(ExamScore.student_id == student_id, Exam.term_id == term_id, Exam.status == "active")
        .order_by(Exam.exam_date, Exam.id)
    ))
    rank_cache: dict[int, dict[int, int | None]] = {}
    row_cache: dict[int, dict[int, object]] = {}
    points: list[StudentExamPoint] = []
    for score in scores:
        if score.exam_id not in rank_cache:
            rows = score_rows(session, score.exam_id, term_id=term_id)
            rank_cache[score.exam_id] = {row.student_id: row.class_rank for row in rows}
            row_cache[score.exam_id] = {row.student_id: row for row in rows}
        row = row_cache[score.exam_id].get(student_id)
        points.append(StudentExamPoint(
            exam_id=score.exam_id,
            exam_name=score.exam.name,
            exam_date=score.exam.exam_date,
            full_score=score.exam.full_score,
            total_score=score.total_score,
            score_rate=round(score.total_score / score.exam.full_score * 100, 2) if score.total_score is not None else None,
            attendance_status=score.attendance_status,
            rank=rank_cache[score.exam_id].get(student_id),
            class_rank=rank_cache[score.exam_id].get(student_id),
            grade_rank=score.grade_rank,
            class_name=row.class_name if row else classroom.name,
            tier=("A" if score.total_score is not None and score.exam.tier_a_cutoff is not None and score.total_score >= score.exam.tier_a_cutoff else
                  "B" if score.total_score is not None and score.exam.tier_b_cutoff is not None and score.total_score >= score.exam.tier_b_cutoff else
                  "C" if score.total_score is not None and score.exam.tier_c_cutoff is not None and score.total_score >= score.exam.tier_c_cutoff else
                  "D" if score.total_score is not None and score.exam.tier_c_cutoff is not None else None),
        ))
    student_data = {
        "id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "class_id": enrollment.class_id,
        "class_name": classroom.name,
        "entrance_english": enrollment.entrance_english,
        "target_score": enrollment.target_score,
        "weak_tags": enrollment.weak_tags,
        "seat": enrollment.seat,
    }
    profile_payload = get_profile_payload(session, student_id, term_id)
    # 兼容在“报告已完成、画像同步修复”之前生成的历史报告：首次打开学生
    # 详情时，将该学生本学期最近一次已完成诊断报告补写为正式画像。
    # 有现成画像时不触碰，避免读取接口覆盖教师已经确认的内容。
    if profile_payload["profile_id"] is None:
        legacy_run = session.scalar(
            select(AnalysisRun)
            .outerjoin(AgentSession, AgentSession.id == AnalysisRun.session_id)
            .where(
                ((AnalysisRun.student_id == student_id) |
                 ((AnalysisRun.student_id.is_(None)) & (AgentSession.student_id == student_id))),
                AnalysisRun.term_id == term_id,
                AnalysisRun.capability.in_(("student_diagnosis", "exam_analysis")),
                AnalysisRun.status.in_(("completed", "degraded")),
            )
            .order_by(AnalysisRun.completed_at.desc(), AnalysisRun.id.desc())
        )
        if legacy_run is not None:
            report = (legacy_run.input_summary_json or {}).get("structured_answer")
            if not isinstance(report, dict):
                # 旧运行可能只在 assistant 消息上保存结构化报告，补查该消息。
                report = session.scalar(
                    select(AgentMessage.structured_answer_json)
                    .where(
                        AgentMessage.analysis_run_id == legacy_run.id,
                        AgentMessage.role == "assistant",
                    )
                    .order_by(AgentMessage.id.desc())
                )
            if isinstance(report, dict):
                try:
                    apply_analysis_report_to_profile(
                        session, run=legacy_run, report=report,
                        confirmed_by="ai", target_student_id=student_id,
                    )
                    profile_payload = get_profile_payload(session, student_id, term_id)
                except Exception:
                    # 学生详情不能因历史报告格式异常而不可用；后续新的报告
                    # 仍会走 submit_report 的同步链路。
                    session.rollback()
    return StudentProfileRead(
        student=student_data,
        exams=points,
        latest_score=points[-1] if points else None,
        question_type_tracking=question_type_tracking(session, student_id, term_id=term_id, scores=scores),
        learning_profile=profile_payload["profile"],
        learning_profile_meta={
            "profile_id": profile_payload["profile_id"],
            "version": profile_payload["version"],
            "updated_at": profile_payload["updated_at"],
            "updated_by": profile_payload["updated_by"],
            "longitudinal_version": profile_payload["longitudinal_version"],
            "needs_compression": profile_payload["needs_compression"],
            "inherited": profile_payload["inherited"],
            "inherited_from_term_id": profile_payload["inherited_from_term_id"],
        },
    )
