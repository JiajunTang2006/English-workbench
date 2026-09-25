from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Class, Enrollment, Exam, ExamScore, Student


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sync_workspace_domains(session: Session, term_id: int, state: dict) -> None:
    """Upsert compatibility-state changes into normalized core tables.

    This bridge never deletes or archives records. Destructive changes must use
    an explicit domain endpoint, preventing partial legacy snapshots from being
    misinterpreted as deletion requests.
    """
    if not isinstance(state, dict):
        return
    students_data = [item for item in state.get("students", []) if isinstance(item, dict)]
    class_names = {str(value).strip() for value in state.get("classes", []) if str(value).strip()}
    class_names.update(str(item.get("class", "")).strip() for item in students_data if str(item.get("class", "")).strip())
    existing_classes = {item.name: item for item in session.scalars(select(Class).where(Class.term_id == term_id))}
    class_map: dict[str, Class] = {}
    for name in sorted(class_names):
        classroom = existing_classes.get(name)
        if classroom is None:
            classroom = Class(term_id=term_id, name=name)
            session.add(classroom)
            session.flush()
        class_map[name] = classroom

    student_by_number: dict[str, Student] = {}
    for raw in students_data:
        student_no = str(raw.get("id", raw.get("student_no", ""))).strip()
        name = str(raw.get("name", "")).strip()
        classroom = class_map.get(str(raw.get("class", "")).strip())
        if not student_no or not name or classroom is None:
            continue
        student = session.scalar(select(Student).where(Student.student_no == student_no))
        if student is None:
            student = Student(student_no=student_no, name=name, class_id=classroom.id)
            session.add(student)
            session.flush()
        student.name = name
        student.status = "active"
        entrance_english = _number(raw.get("english"))
        target_score = _number(raw.get("target"))
        student.entrance_english = entrance_english
        student.target_score = target_score
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.term_id == term_id,
            Enrollment.student_id == student.id,
        ))
        if enrollment is None:
            enrollment = Enrollment(term_id=term_id, class_id=classroom.id, student_id=student.id)
            session.add(enrollment)
        enrollment.class_id = classroom.id
        enrollment.status = "active"
        enrollment.entrance_english = entrance_english
        enrollment.target_score = target_score
        enrollment.weak_tags = str(raw.get("weakTags", "")).strip() or None
        enrollment.parent_phone = str(raw.get("phone", "")).strip() or None
        enrollment.seat = str(raw.get("seat", "")).strip() or None
        student_by_number[student_no] = student

    exams_data = [item for item in state.get("exams", []) if isinstance(item, dict)]
    existing_exams = {
        item.source_key: item for item in session.scalars(select(Exam).where(Exam.term_id == term_id))
        if item.source_key
    }
    for index, raw in enumerate(exams_data):
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        source_key = str(raw.get("id") or f"workspace_exam_{index}")[:100]
        exam = existing_exams.get(source_key)
        if exam is None:
            exam = Exam(term_id=term_id, source_key=source_key, name=name)
            session.add(exam)
            session.flush()
        exam.name = name
        exam.exam_date = _date(raw.get("date", raw.get("exam_date")))
        exam.full_score = max(_number(raw.get("fullScore", raw.get("full_score", 100))) or 100, 0.5)
        exam.exam_type = str(raw.get("type", raw.get("exam_type", "english_total")))
        exam.exam_kind = "entrance" if raw.get("examKind") == "entrance" else "regular"
        lines = raw.get("tierLines") if isinstance(raw.get("tierLines"), dict) else {}
        exam.tier_a_cutoff = _number(lines.get("a"))
        exam.tier_b_cutoff = _number(lines.get("b"))
        exam.tier_c_cutoff = _number(lines.get("c"))
        exam.status = "active"
        raw_scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
        for student_no, raw_score in raw_scores.items():
            student = student_by_number.get(str(student_no))
            if student is None:
                continue
            score_data = raw_score if isinstance(raw_score, dict) else {"英语": raw_score}
            score = session.scalar(select(ExamScore).where(
                ExamScore.exam_id == exam.id,
                ExamScore.student_id == student.id,
            ))
            if score is None:
                score = ExamScore(exam_id=exam.id, student_id=student.id)
                session.add(score)
            score.total_score = _number(score_data.get("英语", score_data.get("total_score")))
            rank = _number(score_data.get("gradeRank", score_data.get("grade_rank")))
            score.grade_rank = int(rank) if rank is not None and rank > 0 and rank.is_integer() else None
            score.attendance_status = "absent" if score_data.get("attendanceStatus") == "absent" else "present"
            class_name = str(score_data.get("classAtExam", "")).strip()
            score.class_id_at_exam = class_map.get(class_name).id if class_name in class_map else student.class_id
