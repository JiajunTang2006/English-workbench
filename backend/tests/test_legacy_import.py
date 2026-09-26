from __future__ import annotations

import json

from sqlalchemy import select

from backend.app.database import create_session_factory
from backend.app.models import AppSetting, Exam, ExamClassMetric, ExamScore, ImportJob, Student, WorkspaceState
from tools.import_legacy_json import import_legacy


def test_legacy_import_migrates_settings_exams_and_scores_idempotently(tmp_path):
    source = tmp_path / "backup.json"
    source.write_text(json.dumps({
        "schema": 1,
        "teacher": {"name": "冯老师", "subject": "初中英语"},
        "classes": ["711"],
        "settings": {"excellent": 90, "pass": 60, "criticalLow": 55},
        "students": [
            {"id": "20261101", "name": "张三", "class": "711", "english": 78, "target": 90},
        ],
        "exams": [{
            "id": "exam-september", "name": "9月月考", "date": "2026-09-28",
            "fullScore": 100, "type": "english_total",
            "scores": {"20261101": {"英语": 82}},
            "classGradeRanks": {"711": 3},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    database_url = f"sqlite:///{tmp_path / 'workbench.db'}"

    first = import_legacy(source, database_url)
    assert first["students"] == 1
    assert first["exams"] == 1
    assert first["scores"] == 1
    assert first["duplicate"] is False

    factory = create_session_factory(database_url)
    with factory() as session:
        student = session.scalar(select(Student).where(Student.student_no == "20261101"))
        exam = session.scalar(select(Exam).where(Exam.source_key == "exam-september"))
        score = session.scalar(select(ExamScore).where(ExamScore.exam_id == exam.id, ExamScore.student_id == student.id))
        assert score.total_score == 82
        assert session.scalar(select(ExamClassMetric)).grade_rank == 3
        assert session.get(AppSetting, "teacher_name").value_json == "冯老师"
        assert session.get(AppSetting, "excellent_line").value_json == 90
        active_term_id = int(session.get(AppSetting, "active_term_id").value_json)
        workspace = session.get(WorkspaceState, active_term_id)
        assert workspace.revision == 1
        assert workspace.state_json["students"][0]["name"] == "张三"

    second = import_legacy(source, database_url)
    assert second["duplicate"] is True
    with factory() as session:
        assert len(list(session.scalars(select(Exam)))) == 1
        assert len(list(session.scalars(select(ExamScore)))) == 1
        assert len(list(session.scalars(select(ImportJob)))) == 1
