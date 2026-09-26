"""Evaluation lifecycle tests: create -> edit -> confirm -> archive + audit trail.

Validates the report confirmation closed loop (Task #78).
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.entities import Term, Exam, Student

AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client_and_ids(tmp_path):
    """Create app + seed term/exam/student for evaluation tests."""
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    Session = app.state.session_factory
    with Session() as s:
        term = Term(code="2025-01", name="2025春", starts_on=date(2025, 2, 1), ends_on=date(2025, 7, 1))
        s.add(term)
        s.commit()
        s.refresh(term)

        exam = Exam(term_id=term.id, name="期中考试", full_score=100,
                     exam_type="english_total", exam_kind="regular", status="active")
        s.add(exam)
        s.commit()
        s.refresh(exam)

        student = Student(student_no="S001", name="张三")
        s.add(student)
        s.commit()
        s.refresh(student)

        yield client, term.id, exam.id, student.id


class TestEvaluationLifecycle:
    """Full report confirmation closed loop."""

    def test_create_draft_evaluation(self, client_and_ids):
        """POST /evaluations creates a draft from AI report text."""
        client, term_id, exam_id, student_id = client_and_ids
        resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 草稿",
            "evidence_snapshot": {"ev_1": {"source": "read_exam_overview", "fact": "avg 76.4"}},
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "draft"
        assert data["ai_original_text"] is not None
        assert data["teacher_confirmed_text"] is None

    def test_edit_draft_teacher_text(self, client_and_ids):
        """PATCH /evaluations/{id} edits teacher preview text on a draft."""
        client, term_id, exam_id, student_id = client_and_ids
        create_resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 原文",
        })
        eval_id = create_resp.json()["id"]
        edit_resp = client.patch(f"/api/v1/agent/evaluations/{eval_id}", headers=AUTH_HEADERS, json={
            "teacher_confirmed_text": "教师修改后",
        })
        assert edit_resp.status_code == 200
        data = edit_resp.json()
        assert data["teacher_confirmed_text"] == "教师修改后"
        assert data["ai_original_text"] == "AI 原文"

    def test_confirm_evaluation(self, client_and_ids):
        """POST /evaluations/{id}/confirm transitions draft -> confirmed."""
        client, term_id, exam_id, student_id = client_and_ids
        create_resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 原文",
        })
        eval_id = create_resp.json()["id"]
        confirm_resp = client.post(f"/api/v1/agent/evaluations/{eval_id}/confirm", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "teacher_confirmed_text": "教师确认文本",
        })
        assert confirm_resp.status_code == 200
        data = confirm_resp.json()
        assert data["status"] == "confirmed"
        assert data["confirmed_at"] is not None

    def test_archive_evaluation(self, client_and_ids):
        """POST /evaluations/{id}/archive transitions confirmed -> archived."""
        client, term_id, exam_id, student_id = client_and_ids
        create_resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 原文",
        })
        eval_id = create_resp.json()["id"]
        client.post(f"/api/v1/agent/evaluations/{eval_id}/confirm", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "teacher_confirmed_text": "确认",
        })
        archive_resp = client.post(f"/api/v1/agent/evaluations/{eval_id}/archive", headers=AUTH_HEADERS)
        assert archive_resp.status_code == 200
        assert archive_resp.json()["status"] == "archived"

    def test_audit_trail(self, client_and_ids):
        """GET /evaluations/{id}/audit returns the full audit trail in order."""
        client, term_id, exam_id, student_id = client_and_ids
        create_resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 原文",
        })
        eval_id = create_resp.json()["id"]
        client.patch(f"/api/v1/agent/evaluations/{eval_id}", headers=AUTH_HEADERS, json={
            "teacher_confirmed_text": "编辑后",
        })
        client.post(f"/api/v1/agent/evaluations/{eval_id}/confirm", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "teacher_confirmed_text": "确认",
        })
        client.post(f"/api/v1/agent/evaluations/{eval_id}/archive", headers=AUTH_HEADERS)
        audit_resp = client.get(f"/api/v1/agent/evaluations/{eval_id}/audit", headers=AUTH_HEADERS)
        assert audit_resp.status_code == 200
        actions = [entry["action"] for entry in audit_resp.json()]
        assert actions == ["created", "edited", "confirmed", "archived"]

    def test_cannot_edit_confirmed(self, client_and_ids):
        """PATCH on a confirmed evaluation returns 409."""
        client, term_id, exam_id, student_id = client_and_ids
        create_resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 原文",
        })
        eval_id = create_resp.json()["id"]
        client.post(f"/api/v1/agent/evaluations/{eval_id}/confirm", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "teacher_confirmed_text": "确认",
        })
        edit_resp = client.patch(f"/api/v1/agent/evaluations/{eval_id}", headers=AUTH_HEADERS, json={
            "teacher_confirmed_text": "试图修改",
        })
        assert edit_resp.status_code == 409

    def test_cannot_archive_draft(self, client_and_ids):
        """POST archive on a draft returns 409."""
        client, term_id, exam_id, student_id = client_and_ids
        create_resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 原文",
        })
        eval_id = create_resp.json()["id"]
        archive_resp = client.post(f"/api/v1/agent/evaluations/{eval_id}/archive", headers=AUTH_HEADERS)
        assert archive_resp.status_code == 409

    def test_list_evaluations(self, client_and_ids):
        """GET /evaluations lists evaluations with optional filters."""
        client, term_id, exam_id, student_id = client_and_ids
        client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "草稿1",
        })
        client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "草稿2",
        })
        resp = client.get(f"/api/v1/agent/evaluations?student_id={student_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_evaluation_by_id(self, client_and_ids):
        """GET /evaluations/{id} returns a single evaluation."""
        client, term_id, exam_id, student_id = client_and_ids
        create_resp = client.post("/api/v1/agent/evaluations", headers=AUTH_HEADERS, json={
            "student_id": student_id, "term_id": term_id, "exam_id": exam_id,
            "ai_original_text": "AI 原文",
        })
        eval_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/agent/evaluations/{eval_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["id"] == eval_id
