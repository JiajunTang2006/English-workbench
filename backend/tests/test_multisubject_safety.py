"""Regression checks for subject boundaries and persisted teacher materials."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import Exam, Term
from backend.app.models.agent_entities import AgentMessage, AgentSession, AnalysisRun
from backend.app.models.entities import Class, Enrollment, Student


HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _active_term(db):
    return db.scalar(select(Term).where(Term.status == "active"))


def test_subject_switch_rejected_after_exam_without_changing_setting(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        with app.state.session_factory() as db:
            term = _active_term(db)
            db.add(Exam(term_id=term.id, name="期中", full_score=100))
            db.commit()
        response = client.patch("/api/v1/settings", headers=HEADERS, json={"subject_key": "math"})
        assert response.status_code == 409
        assert client.get("/api/v1/subjects", headers=HEADERS).json()["current"] == "english"


def test_non_english_profile_is_tagged_and_stays_in_its_workspace(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        with app.state.session_factory() as db:
            term = _active_term(db)
            classroom = Class(term_id=term.id, name="一班")
            student = Student(student_no="M001", name="测试学生")
            db.add_all([classroom, student])
            db.flush()
            db.add(Enrollment(term_id=term.id, class_id=classroom.id, student_id=student.id, status="active"))
            db.commit()
            term_id, student_id = term.id, student.id
        assert client.patch("/api/v1/settings", headers=HEADERS, json={"subject_key": "math"}).status_code == 200
        response = client.patch(
            f"/api/v1/students/{student_id}/profile?term_id={term_id}",
            headers=HEADERS,
            json={"expected_version": 0, "patch": {"summary": "方程建模需要练习"}},
        )
        assert response.status_code == 200, response.text
        profile = client.get(f"/api/v1/students/{student_id}/profile?term_id={term_id}", headers=HEADERS)
        assert profile.json()["learning_profile"]["subject_key"] == "math"
        assert client.patch("/api/v1/settings", headers=HEADERS, json={"subject_key": "english"}).status_code == 409


def test_teacher_material_edit_persists_and_original_remains_readable(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        with app.state.session_factory() as db:
            term = _active_term(db)
            conversation = AgentSession(term_id=term.id, subject_key="english", title="教学包")
            db.add(conversation)
            db.flush()
            run = AnalysisRun(session_id=conversation.id, term_id=term.id,
                              subject_key="english", capability="review_plan", status="completed")
            db.add(run)
            db.flush()
            report = {"answer_type": "review_plan", "sections": [
                {"kind": "lesson_flow", "title": "讲评", "body": "原始讲评", "items": []},
                {"kind": "student_handout", "title": "练习", "body": "原始学生练习", "items": ["原始题"]},
                {"kind": "teacher_key", "title": "答案", "body": "原始答案", "items": []},
                {"kind": "followup_assessment", "title": "复测", "body": "原始复测", "items": []},
            ]}
            db.add(AgentMessage(session_id=conversation.id, analysis_run_id=run.id,
                                role="assistant", structured_answer_json=report))
            db.commit()
            session_id, run_id = conversation.id, run.id
        saved = client.put(f"/api/v1/agent/runs/{run_id}/materials/1", headers=HEADERS,
                           json={"body": "教师修改的练习", "items": ["新题"]})
        assert saved.status_code == 200, saved.text
        messages = client.get(f"/api/v1/agent/sessions/{session_id}/messages", headers=HEADERS)
        assert messages.status_code == 200, messages.text
        message = messages.json()[0]
        assert message["structured_answer"]["sections"][1]["body"] == "原始学生练习"
        assert message["material_edits"]["1"] == {"body": "教师修改的练习", "items": ["新题"]}
        assert client.put(f"/api/v1/agent/runs/{run_id}/materials/9", headers=HEADERS,
                          json={"body": "无效", "items": []}).status_code == 404
