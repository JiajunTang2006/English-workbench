"""学生画像：AI 草稿与教师确认闭环。"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.agent_entities import AnalysisRun, ExamPaperVersion, ExamQuestion, StudentItemResult, StudentLongitudinalProfile, StudentProfile
from backend.app.models.entities import Class, Enrollment, Exam, ExamScore, Student, Term
from sqlalchemy import func, select


AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def profile_client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    client = TestClient(app)
    with app.state.session_factory() as session:
        term = Term(code="2026-spring", name="2026 春", starts_on=date(2026, 2, 1), ends_on=date(2026, 7, 1))
        session.add(term)
        session.flush()
        classroom = Class(term_id=term.id, name="九年级一班")
        student = Student(student_no="S-PROFILE-001", name="测试学生")
        session.add_all([classroom, student])
        session.flush()
        session.add(Enrollment(term_id=term.id, class_id=classroom.id, student_id=student.id, status="active"))
        session.commit()
        yield client, term.id, student.id


def test_profile_revision_requires_confirmation(profile_client):
    client, term_id, student_id = profile_client
    response = client.post("/api/v1/agent/profile-revisions", headers=AUTH_HEADERS, json={
        "student_id": student_id,
        "term_id": term_id,
        "patch": {"strengths_add": ["阅读定位稳定"], "goals_add": ["下次阅读得分率达到 80%"]},
        "evidence_ids": ["ev_1"],
    })
    assert response.status_code == 201
    revision_id = response.json()["id"]

    profile = client.get(f"/api/v1/students/{student_id}/profile?term_id={term_id}", headers=AUTH_HEADERS)
    assert profile.status_code == 200
    assert profile.json()["learning_profile_meta"]["version"] == 0
    assert profile.json()["learning_profile"]["strengths"] == []

    confirmed = client.post(f"/api/v1/agent/profile-revisions/{revision_id}/confirm", headers=AUTH_HEADERS)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    profile = client.get(f"/api/v1/students/{student_id}/profile?term_id={term_id}", headers=AUTH_HEADERS)
    assert profile.json()["learning_profile_meta"]["version"] == 1
    assert profile.json()["learning_profile"]["strengths"] == ["阅读定位稳定"]


def test_profile_revision_reject_does_not_change_snapshot(profile_client):
    client, term_id, student_id = profile_client
    response = client.post("/api/v1/agent/profile-revisions", headers=AUTH_HEADERS, json={
        "student_id": student_id,
        "term_id": term_id,
        "patch": {"weaknesses_add": ["时态辨析需巩固"]},
        "evidence_ids": ["ev_2"],
    })
    revision_id = response.json()["id"]
    rejected = client.post(f"/api/v1/agent/profile-revisions/{revision_id}/reject", headers=AUTH_HEADERS, json={"reason": "教师复核后暂不记录"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    profile = client.get(f"/api/v1/students/{student_id}/profile?term_id={term_id}", headers=AUTH_HEADERS)
    assert profile.json()["learning_profile_meta"]["version"] == 0
    assert profile.json()["learning_profile"]["weaknesses"] == []


def test_teacher_can_edit_formal_profile_directly(profile_client):
    client, term_id, student_id = profile_client
    response = client.patch(
        f"/api/v1/students/{student_id}/profile?term_id={term_id}",
        headers=AUTH_HEADERS,
        json={
            "expected_version": 0,
            "patch": {
                "summary": "需要加强阅读定位",
                "weaknesses": ["阅读定位需巩固"],
                "goals": ["下次阅读得分率达到 80%"],
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["learning_profile_meta"]["version"] == 1
    assert body["learning_profile"]["weaknesses"] == ["阅读定位需巩固"]
    assert body["learning_profile"]["goals"] == ["下次阅读得分率达到 80%"]


def test_profile_endpoint_accepts_student_number(profile_client):
    client, term_id, student_id = profile_client
    response = client.patch(
        f"/api/v1/students/S-PROFILE-001/profile?term_id={term_id}",
        headers=AUTH_HEADERS,
        json={"patch": {"summary": "按学号访问也能更新"}, "expected_version": 0},
    )
    assert response.status_code == 200, response.text
    assert response.json()["student"]["id"] == student_id
    assert response.json()["learning_profile"]["summary"] == "按学号访问也能更新"


def test_profile_question_type_tracking_averages_exam_level_rates(profile_client):
    client, term_id, student_id = profile_client
    with client as active_client:
        session = active_client.app.state.session_factory()
        try:
            classroom = session.scalar(select(Class).where(Class.term_id == term_id))
            exams = [
                Exam(term_id=term_id, source_key="exam-a", name="第一次月考", exam_date=date(2026, 3, 10), full_score=100),
                Exam(term_id=term_id, source_key="exam-b", name="第二次月考", exam_date=date(2026, 4, 10), full_score=100),
            ]
            session.add_all(exams)
            session.flush()
            for exam, listening_score, reading_score in ((exams[0], 9, 4), (exams[1], 10, 3)):
                version = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed", full_score=100)
                session.add(version)
                session.flush()
                listening = ExamQuestion(paper_version_id=version.id, question_no="1", section_name="听力", question_type="选择题", max_score=10)
                reading = ExamQuestion(paper_version_id=version.id, question_no="2", section_name="阅读", question_type="阅读理解", max_score=5)
                session.add_all([listening, reading])
                session.flush()
                session.add(ExamScore(exam_id=exam.id, student_id=student_id, class_id_at_exam=classroom.id, total_score=listening_score + reading_score))
                session.add_all([
                    StudentItemResult(exam_id=exam.id, student_id=student_id, question_id=listening.id, score=listening_score),
                    StudentItemResult(exam_id=exam.id, student_id=student_id, question_id=reading.id, score=reading_score),
                ])
            session.commit()
        finally:
            session.close()

    body = client.get(f"/api/v1/students/{student_id}/profile?term_id={term_id}", headers=AUTH_HEADERS).json()
    tracking = body["question_type_tracking"]
    averages = {item["type"]: item for item in tracking["averages"]}
    assert tracking["exam_count"] == 2
    assert averages["听力理解"]["score_rate"] == 95.0
    assert averages["阅读理解"]["score_rate"] == 70.0
    assert averages["完形填空"]["score_rate"] is None


def test_completed_diagnosis_report_is_visible_in_student_profile(profile_client):
    client, term_id, student_id = profile_client
    with client as active_client:
        session = active_client.app.state.session_factory()
        try:
            run = AnalysisRun(
                session_id=None,
                term_id=term_id,
                capability="student_diagnosis",
                student_id=student_id,
                status="completed",
                input_summary_json={
                    "structured_answer": {
                        "answer_type": "student_diagnosis",
                        "summary": "阅读定位较稳定，但语法基础仍需巩固。",
                        "findings": [{
                            "title": "语法薄弱",
                            "description": "时态辨析失分较多",
                            "evidence_ids": ["ev-report"],
                        }],
                        "recommendations": [{
                            "action": "安排时态辨析专项练习",
                            "supports": ["ev-report"],
                        }],
                    },
                },
            )
            session.add(run)
            session.commit()
        finally:
            session.close()

    response = client.get(
        f"/api/v1/students/{student_id}/profile?term_id={term_id}",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["learning_profile_meta"]["version"] == 1
    assert body["learning_profile"]["summary"] == "阅读定位较稳定，但语法基础仍需巩固。"
    assert body["learning_profile"]["weaknesses"] == ["语法薄弱：时态辨析失分较多"]
    assert body["learning_profile"]["interventions"] == ["安排时态辨析专项练习"]
    again = client.get(
        f"/api/v1/students/{student_id}/profile?term_id={term_id}",
        headers=AUTH_HEADERS,
    ).json()
    assert again["learning_profile_meta"]["version"] == 1


def test_profile_continues_across_terms_and_updates_longitudinal_snapshot(profile_client):
    client, source_term_id, student_id = profile_client
    first = client.post("/api/v1/agent/profile-revisions", headers=AUTH_HEADERS, json={
        "student_id": student_id,
        "term_id": source_term_id,
        "patch": {"strengths_add": ["阅读定位稳定"], "goals_add": ["保持稳定输出"]},
        "evidence_ids": ["ev_source"],
    })
    assert first.status_code == 201
    assert client.post(f"/api/v1/agent/profile-revisions/{first.json()['id']}/confirm", headers=AUTH_HEADERS).status_code == 200

    # 先切换到来源学期，再创建下一学期，确保切换接口能知道画像的来源。
    switched_source = client.put("/api/v1/terms/current", headers=AUTH_HEADERS, json={"term_id": source_term_id})
    assert switched_source.status_code == 200, switched_source.text
    next_term = client.post("/api/v1/terms", headers=AUTH_HEADERS, json={
        "code": "2026-autumn",
        "name": "2026 秋",
        "clone_from_term_id": source_term_id,
        "clone_classes": True,
        "clone_enrollments": True,
    })
    assert next_term.status_code == 201, next_term.text
    next_term_id = next_term.json()["id"]
    switched_next = client.put("/api/v1/terms/current", headers=AUTH_HEADERS, json={"term_id": next_term_id})
    assert switched_next.status_code == 200, switched_next.text
    assert client.put("/api/v1/terms/current", headers=AUTH_HEADERS, json={"term_id": next_term_id}).status_code == 200

    inherited = client.get(f"/api/v1/students/{student_id}/profile?term_id={next_term_id}", headers=AUTH_HEADERS)
    assert inherited.status_code == 200, inherited.text
    body = inherited.json()
    assert body["learning_profile"]["strengths"] == ["阅读定位稳定"]
    assert body["learning_profile_meta"]["inherited"] is True
    assert body["learning_profile_meta"]["needs_compression"] is True

    second = client.post("/api/v1/agent/profile-revisions", headers=AUTH_HEADERS, json={
        "student_id": student_id,
        "term_id": next_term_id,
        "patch": {"weaknesses_add": ["长难句需要持续巩固"]},
        "evidence_ids": ["ev_next"],
    })
    assert second.status_code == 201, second.text
    confirmed = client.post(f"/api/v1/agent/profile-revisions/{second.json()['id']}/confirm", headers=AUTH_HEADERS)
    assert confirmed.status_code == 200, confirmed.text

    updated = client.get(f"/api/v1/students/{student_id}/profile?term_id={next_term_id}", headers=AUTH_HEADERS).json()
    assert updated["learning_profile"]["strengths"] == ["阅读定位稳定"]
    assert updated["learning_profile"]["weaknesses"] == ["长难句需要持续巩固"]
    assert updated["learning_profile_meta"]["needs_compression"] is False
    with client as active_client:
        session = active_client.app.state.session_factory()
        try:
            longitudinal = session.scalar(select(StudentLongitudinalProfile).where(
                StudentLongitudinalProfile.student_id == student_id,
            ))
            assert longitudinal is not None
            assert longitudinal.profile_json["strengths"] == ["阅读定位稳定"]
            assert longitudinal.profile_json["weaknesses"] == ["长难句需要持续巩固"]
            assert session.scalar(select(func.count(StudentProfile.id)).where(
                StudentProfile.student_id == student_id,
                StudentProfile.term_id == next_term_id,
            )) == 1
        finally:
            session.close()
