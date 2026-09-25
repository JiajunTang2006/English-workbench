from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from backend.app.config import get_settings
from backend.app.database import create_session_factory, run_migrations
from backend.app.models import AppSetting, Class, Enrollment, Exam, ExamClassMetric, ExamScore, ImportJob, Student, WorkspaceState
from backend.app.services.terms import current_term_id
from sqlalchemy import select


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def import_legacy(source: Path, database_url: str) -> dict[str, int | str | bool]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON 根节点必须是对象")
    schema = payload.get("schema", 1)
    if not isinstance(schema, int) or schema > 3:
        raise ValueError(f"不支持的旧数据版本：{schema}")
    classes = payload.get("classes") or []
    students = payload.get("students") or []
    exams = payload.get("exams") or []
    if not isinstance(classes, list) or not isinstance(students, list) or not isinstance(exams, list):
        raise ValueError("classes、students 和 exams 必须是数组")

    run_migrations(database_url)
    factory = create_session_factory(database_url)
    score_count = sum(len(item.get("scores", {})) for item in exams if isinstance(item, dict))
    stats: dict[str, int | str | bool] = {
        "read": len(classes) + len(students) + len(exams) + score_count,
        "created": 0, "updated": 0, "skipped": 0, "errors": 0,
        "classes": 0, "students": 0, "exams": 0, "scores": 0, "class_metrics": 0,
        "duplicate": False,
    }
    source_digest = sha256(source)
    with factory() as session:
        try:
            term_id = current_term_id(session)
            completed = session.scalar(
                select(ImportJob)
                .where(ImportJob.source_sha256 == source_digest, ImportJob.status == "completed")
                .order_by(ImportJob.id.desc())
            )
            if completed:
                if session.get(WorkspaceState, term_id) is None:
                    session.add(WorkspaceState(term_id=term_id, state_json=payload, revision=1))
                    session.commit()
                previous = dict(completed.report_json or {})
                previous["duplicate"] = True
                return previous
            class_map: dict[str, Class] = {}
            declared_classes = list(classes)
            for raw in students:
                if isinstance(raw, dict):
                    name = str(raw.get("class", raw.get("class_name", ""))).strip()
                    if name and name not in declared_classes:
                        declared_classes.append(name)
            for raw in declared_classes:
                if isinstance(raw, str):
                    name = raw.strip()
                elif isinstance(raw, dict):
                    name = str(raw.get("name", raw.get("class", ""))).strip()
                else:
                    name = ""
                if not name:
                    stats["errors"] += 1
                    continue
                item = session.scalar(select(Class).where(Class.term_id == term_id, Class.name == name))
                if item is None:
                    item = Class(term_id=term_id, name=name)
                    session.add(item)
                    session.flush()
                    stats["created"] += 1
                else:
                    stats["skipped"] += 1
                class_map[name] = item
            stats["classes"] = len(class_map)
            student_map: dict[str, Student] = {}
            for raw in students:
                if not isinstance(raw, dict):
                    stats["errors"] += 1
                    continue
                student_no = str(raw.get("id", raw.get("student_no", ""))).strip()
                name = str(raw.get("name", "")).strip()
                class_name = str(raw.get("class", raw.get("class_name", ""))).strip()
                if not student_no or not name or class_name not in class_map:
                    stats["errors"] += 1
                    continue
                item = session.scalar(select(Student).where(Student.student_no == student_no))
                values = {"student_no": student_no, "name": name, "class_id": class_map[class_name].id, "gender": raw.get("gender"), "entrance_english": raw.get("english"), "target_score": raw.get("target") or None, "weak_tags": raw.get("weakTags"), "parent_phone": raw.get("phone"), "seat": raw.get("seat")}
                if item is None:
                    item = Student(**values)
                    session.add(item)
                    stats["created"] += 1
                else:
                    for key, value in values.items():
                        setattr(item, key, value)
                    stats["updated"] += 1
                session.flush()
                enrollment = session.scalar(select(Enrollment).where(
                    Enrollment.term_id == term_id,
                    Enrollment.student_id == item.id,
                ))
                if enrollment is None:
                    session.add(Enrollment(term_id=term_id, class_id=class_map[class_name].id, student_id=item.id))
                else:
                    enrollment.class_id = class_map[class_name].id
                student_map[student_no] = item
            stats["students"] = len(student_map)

            legacy_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
            teacher = payload.get("teacher") if isinstance(payload.get("teacher"), dict) else {}
            settings_map = {
                "teacher_name": teacher.get("name", legacy_settings.get("teacher_name")),
                "subject": teacher.get("subject", legacy_settings.get("subject")),
                "excellent_line": legacy_settings.get("excellent", legacy_settings.get("excellent_line")),
                "passing_line": legacy_settings.get("pass", legacy_settings.get("passing_line")),
                "critical_line": legacy_settings.get("criticalLow", legacy_settings.get("critical_line")),
            }
            for key, value in settings_map.items():
                if value is not None:
                    item = session.get(AppSetting, key) or AppSetting(key=key, value_json=value)
                    item.value_json = value
                    session.add(item)

            for index, raw in enumerate(exams):
                if not isinstance(raw, dict):
                    stats["errors"] += 1
                    continue
                name = str(raw.get("name", "")).strip()
                if not name:
                    stats["errors"] += 1
                    continue
                source_key = str(raw.get("id") or f"legacy_exam_{index}_{name}")[:100]
                exam = session.scalar(select(Exam).where(Exam.term_id == term_id, Exam.source_key == source_key))
                values = {
                    "term_id": term_id,
                    "source_key": source_key,
                    "name": name,
                    "exam_date": _parse_date(raw.get("date", raw.get("exam_date"))),
                    "full_score": float(raw.get("fullScore", raw.get("full_score", 100)) or 100),
                    "exam_type": str(raw.get("type", raw.get("exam_type", "english_total"))),
                }
                if values["full_score"] <= 0:
                    stats["errors"] += 1
                    continue
                if exam is None:
                    exam = Exam(**values)
                    session.add(exam)
                    session.flush()
                    stats["created"] += 1
                else:
                    for key, value in values.items():
                        setattr(exam, key, value)
                    stats["updated"] += 1
                stats["exams"] += 1
                raw_scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
                for student_no, score_data in raw_scores.items():
                    student = student_map.get(str(student_no).strip())
                    if not student:
                        stats["errors"] += 1
                        continue
                    if isinstance(score_data, dict):
                        score_value = score_data.get("英语", score_data.get("total_score"))
                        grade_rank_value = score_data.get("年级排名", score_data.get("gradeRank", score_data.get("grade_rank")))
                    else:
                        score_value = score_data
                        grade_rank_value = None
                    if score_value in (None, ""):
                        continue
                    try:
                        numeric_score = float(score_value)
                    except (TypeError, ValueError):
                        stats["errors"] += 1
                        continue
                    if numeric_score < 0 or numeric_score > exam.full_score:
                        stats["errors"] += 1
                        continue
                    record = session.scalar(select(ExamScore).where(
                        ExamScore.exam_id == exam.id,
                        ExamScore.student_id == student.id,
                    ))
                    if record is None:
                        record = ExamScore(exam_id=exam.id, student_id=student.id)
                        session.add(record)
                        stats["created"] += 1
                    else:
                        stats["updated"] += 1
                    record.total_score = numeric_score
                    record.grade_rank = None
                    if grade_rank_value not in (None, ""):
                        try:
                            numeric_rank = int(grade_rank_value)
                            if numeric_rank <= 0 or float(grade_rank_value) != numeric_rank:
                                raise ValueError
                            record.grade_rank = numeric_rank
                        except (TypeError, ValueError):
                            stats["errors"] += 1
                    record.attendance_status = "present"
                    stats["scores"] += 1

                raw_metrics = raw.get("classGradeRanks") if isinstance(raw.get("classGradeRanks"), dict) else {}
                for class_name, grade_rank_value in raw_metrics.items():
                    classroom = class_map.get(str(class_name).strip())
                    if not classroom:
                        stats["errors"] += 1
                        continue
                    try:
                        numeric_rank = int(grade_rank_value)
                        if numeric_rank <= 0 or float(grade_rank_value) != numeric_rank:
                            raise ValueError
                    except (TypeError, ValueError):
                        stats["errors"] += 1
                        continue
                    metric = session.scalar(select(ExamClassMetric).where(
                        ExamClassMetric.exam_id == exam.id,
                        ExamClassMetric.class_id == classroom.id,
                    ))
                    if metric is None:
                        metric = ExamClassMetric(exam_id=exam.id, class_id=classroom.id, grade_rank=numeric_rank)
                        session.add(metric)
                        stats["created"] += 1
                    else:
                        metric.grade_rank = numeric_rank
                        stats["updated"] += 1
                    stats["class_metrics"] += 1

            workspace = session.get(WorkspaceState, term_id)
            if workspace is None:
                session.add(WorkspaceState(term_id=term_id, state_json=payload, revision=1))
            else:
                workspace.state_json = payload
                workspace.revision += 1

            session.add(ImportJob(source_sha256=source_digest, source_name=source.name, status="completed", report_json=stats))
            session.commit()
        except Exception:
            session.rollback()
            raise
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移旧工作台 JSON 到 SQLite")
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    print(json.dumps(import_legacy(args.source, settings.database_url), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
