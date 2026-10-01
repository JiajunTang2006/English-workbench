"""P1-12: 新建会话提交真实 class_id/exam_id/student_id 回归测试。

验证：
1. create_session 接受真实的数字 class_id / exam_id / student_id 并正确存储
2. create_session 拒绝 Schema 不认识的字段（class_filter 不会被存储）
3. send_message 在 exam_analysis + 无 exam_id 时允许创建运行
4. send_message 在 exam_analysis + 有 exam_id 时正常创建运行
5. student_diagnosis / review_plan 仍执行能力声明的 exam_id 门禁
6. AgentSessionRead 正确返回 class_id / exam_id / student_id 字段
7. 班级名称字符串不会作为 class_id 被接受（类型校验）
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.agent_entities import (
    AgentSession,
    AgentMessageAttachment,
    AnalysisRun,
    Base,
)
from backend.app.models.entities import Term, Class, Exam, ExamScore, Student, Enrollment, Attachment
from backend.app.agent.config import AgentConfig


def _enabled_config() -> AgentConfig:
    base = AgentConfig()
    return replace(
        base,
        feature_flags={
            **base.feature_flags,
            "agent_enabled": True,
            "text_agent_enabled": True,
        },
    )


AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def app_client_db(tmp_path):
    """创建应用、客户端、DB session factory，含完整实体数据。"""
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    Session = app.state.session_factory

    with Session() as s:
        term = Term(code="2024-01", name="2024春", starts_on=date(2024, 2, 1), ends_on=date(2024, 7, 1))
        s.add(term)
        s.commit()
        s.refresh(term)
        term_id = term.id

        cls = Class(term_id=term_id, name="1班", grade="高三", status="active")
        s.add(cls)
        s.commit()
        s.refresh(cls)
        class_id = cls.id

        exam = Exam(
            term_id=term_id, source_key="exam_001",
            name="期中考试", full_score=100,
            exam_type="english_total", exam_kind="regular", status="active",
        )
        s.add(exam)
        s.commit()
        s.refresh(exam)
        exam_id = exam.id

        student = Student(
            student_no="S001", name="张三",
            class_id=class_id, status="active",
        )
        s.add(student)
        s.commit()
        s.refresh(student)
        student_id = student.id

        enrollment = Enrollment(
            term_id=term_id, class_id=class_id,
            student_id=student_id, status="active",
        )
        s.add(enrollment)
        s.commit()

    yield client, Session, term_id, class_id, exam_id, student_id


class TestCreateSessionWithRealIds:
    def test_create_with_class_and_exam(self, app_client_db):
        client, _, term_id, class_id, exam_id, _ = app_client_db
        resp = client.post("/api/v1/agent/sessions", json={"term_id": term_id, "class_id": class_id, "exam_id": exam_id}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["class_id"] == class_id
        assert data["exam_id"] == exam_id

    def test_create_with_all_ids(self, app_client_db):
        client, _, term_id, class_id, exam_id, student_id = app_client_db
        resp = client.post("/api/v1/agent/sessions", json={"term_id": term_id, "class_id": class_id, "exam_id": exam_id, "student_id": student_id}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["class_id"] == class_id
        assert data["exam_id"] == exam_id
        assert data["student_id"] == student_id

    def test_create_minimal(self, app_client_db):
        client, _, term_id, _, _, _ = app_client_db
        resp = client.post("/api/v1/agent/sessions", json={"term_id": term_id}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["class_id"] is None
        assert data["exam_id"] is None

    def test_unknown_class_filter_rejected(self, app_client_db):
        client, _, term_id, _, _, _ = app_client_db
        resp = client.post("/api/v1/agent/sessions", json={"term_id": term_id, "class_filter": "1班"}, headers=AUTH_HEADERS)
        assert resp.status_code == 422


class TestTypeIdValidation:
    def test_string_class_id_rejected(self, app_client_db):
        client, _, term_id, _, _, _ = app_client_db
        resp = client.post("/api/v1/agent/sessions", json={"term_id": term_id, "class_id": "1班"}, headers=AUTH_HEADERS)
        assert resp.status_code == 422

    def test_string_exam_id_rejected(self, app_client_db):
        client, _, term_id, _, _, _ = app_client_db
        resp = client.post("/api/v1/agent/sessions", json={"term_id": term_id, "exam_id": "exam_001"}, headers=AUTH_HEADERS)
        assert resp.status_code == 422


class TestOptionalExamScope:
    def test_exam_analysis_without_exam_id_accepted(self, app_client_db):
        client, Session, term_id, _, _, _ = app_client_db
        with Session() as s:
            agent_session = AgentSession(title="无考试", term_id=term_id, status="active")
            s.add(agent_session)
            s.commit()
            s.refresh(agent_session)
            sid = agent_session.id
        enabled_cfg = _enabled_config()
        with patch("backend.app.routers.agent.get_agent_config", return_value=enabled_cfg):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                resp = client.post(f"/api/v1/agent/sessions/{sid}/messages", json={"content": "分析上传资料", "quick_task": "exam_analysis"}, headers=AUTH_HEADERS)
        assert resp.status_code == 202
        assert "run_id" in resp.json()
        with Session() as s:
            run = s.get(AnalysisRun, resp.json()["run_id"])
            assert run is not None
            assert run.exam_id is None
            assert run.capability == "exam_analysis"

    def test_exam_analysis_without_exam_id_with_attachment_accepted(self, app_client_db, tmp_path):
        client, Session, term_id, _, _, _ = app_client_db
        storage_name = "term_optional_exam_context.txt"
        (tmp_path / "attachments").mkdir(exist_ok=True)
        (tmp_path / "attachments" / storage_name).write_text("班级均分 82 分，词汇题失分较多。", encoding="utf-8")
        with Session() as s:
            attachment = Attachment(
                term_id=term_id,
                title="上传的小分表摘要",
                original_name="score-summary.txt",
                mime_type="text/plain",
                size_bytes=48,
                sha256="a" * 64,
                storage_name=storage_name,
                metadata_json={
                    "parsed": {
                        "status": "pending_review",
                        "content": "班级均分 82 分，词汇题失分较多。",
                        "error": None,
                    }
                },
            )
            agent_session = AgentSession(title="附件分析", term_id=term_id, status="active")
            s.add_all([attachment, agent_session])
            s.commit()
            s.refresh(attachment)
            s.refresh(agent_session)
            aid = attachment.id
            sid = agent_session.id
        enabled_cfg = _enabled_config()
        with patch("backend.app.routers.agent.get_agent_config", return_value=enabled_cfg):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                resp = client.post(
                    f"/api/v1/agent/sessions/{sid}/messages",
                    json={
                        "content": "根据附件分析考试",
                        "quick_task": "exam_analysis",
                        "attachment_ids": [aid],
                    },
                    headers=AUTH_HEADERS,
                )
        assert resp.status_code == 202
        with Session() as s:
            run = s.get(AnalysisRun, resp.json()["run_id"])
            attachment = s.get(Attachment, aid)
            assert run is not None and run.exam_id is None
            assert run.input_summary_json["attachment_ids"] == [aid]
            assert attachment.metadata_json["parsed"]["status"] == "confirmed"
            assert s.query(AgentMessageAttachment).filter_by(
                attachment_id=aid, promoted_to_formal=True,
            ).count() == 1

    def test_exam_analysis_with_exam_id_accepted(self, app_client_db):
        client, Session, term_id, _, exam_id, _ = app_client_db
        with Session() as s:
            agent_session = AgentSession(title="有考试", term_id=term_id, exam_id=exam_id, status="active")
            s.add(agent_session)
            s.commit()
            s.refresh(agent_session)
            sid = agent_session.id
        enabled_cfg = _enabled_config()
        with patch("backend.app.routers.agent.get_agent_config", return_value=enabled_cfg):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                resp = client.post(f"/api/v1/agent/sessions/{sid}/messages", json={"content": "分析", "quick_task": "exam_analysis"}, headers=AUTH_HEADERS)
        assert resp.status_code == 202
        assert "run_id" in resp.json()

    def test_follow_up_inherits_confirmed_attachments(self, app_client_db, tmp_path):
        """同一会话的追问不重复上传也能继续读取上一轮确认过的附件。"""
        client, Session, term_id, _, _, _ = app_client_db
        storage_name = "follow-up-context.txt"
        (tmp_path / "attachments").mkdir(exist_ok=True)
        (tmp_path / "attachments" / storage_name).write_text("听力均分 82 分。", encoding="utf-8")
        with Session() as s:
            attachment = Attachment(
                term_id=term_id,
                title="听力成绩摘要",
                original_name="follow-up-context.txt",
                mime_type="text/plain",
                size_bytes=24,
                sha256="b" * 64,
                storage_name=storage_name,
                metadata_json={"parsed": {"status": "pending_review", "content": "听力均分 82 分。", "error": None}},
            )
            agent_session = AgentSession(title="连续追问", term_id=term_id, status="active")
            s.add_all([attachment, agent_session])
            s.commit()
            s.refresh(attachment)
            s.refresh(agent_session)
            aid, sid = attachment.id, agent_session.id

        enabled_cfg = _enabled_config()
        with patch("backend.app.routers.agent.get_agent_config", return_value=enabled_cfg):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                first = client.post(
                    f"/api/v1/agent/sessions/{sid}/messages",
                    json={"content": "先分析听力表现", "quick_task": "exam_analysis", "attachment_ids": [aid]},
                    headers=AUTH_HEADERS,
                )
                assert first.status_code == 202
                second = client.post(
                    f"/api/v1/agent/sessions/{sid}/messages",
                    json={"content": "请继续给出两条教学建议", "quick_task": "exam_analysis"},
                    headers=AUTH_HEADERS,
                )
        assert second.status_code == 202
        with Session() as s:
            second_run = s.get(AnalysisRun, second.json()["run_id"])
            assert second_run is not None
            assert second_run.input_summary_json["attachment_ids"] == [aid]
            assert s.query(AgentMessageAttachment).filter_by(attachment_id=aid).count() == 2


class TestCapabilityScopeRequirements:
    def test_named_review_plan_binds_verified_student_to_run(self, app_client_db):
        client, Session, term_id, class_id, exam_id, student_id = app_client_db
        with Session() as s:
            s.add(ExamScore(exam_id=exam_id, student_id=student_id,
                            total_score=91.0, attendance_status="present",
                            class_id_at_exam=class_id))
            agent_session = AgentSession(title="全班诊断", term_id=term_id,
                                         exam_id=exam_id, status="active")
            s.add(agent_session)
            s.commit()
            sid = agent_session.id
        with patch("backend.app.routers.agent.get_agent_config", return_value=_enabled_config()):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                response = client.post(
                    f"/api/v1/agent/sessions/{sid}/messages",
                    json={"content": "为张三制定复习计划", "quick_task": "review_plan"},
                    headers=AUTH_HEADERS)
        assert response.status_code == 202
        with Session() as s:
            run = s.get(AnalysisRun, response.json()["run_id"])
            assert run.student_id == student_id
            assert run.exam_id == exam_id

    def test_named_review_plan_missing_score_does_not_create_run(self, app_client_db):
        client, Session, term_id, _, exam_id, _ = app_client_db
        with Session() as s:
            agent_session = AgentSession(title="全班诊断", term_id=term_id,
                                         exam_id=exam_id, status="active")
            s.add(agent_session)
            s.commit()
            sid = agent_session.id
        with patch("backend.app.routers.agent.get_agent_config", return_value=_enabled_config()):
            response = client.post(
                f"/api/v1/agent/sessions/{sid}/messages",
                json={"content": "为张三制定复习计划", "quick_task": "review_plan"},
                headers=AUTH_HEADERS)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "STUDENT_EXAM_SCORE_MISSING"
        with Session() as s:
            assert s.query(AnalysisRun).filter_by(session_id=sid).count() == 0

    def test_student_diagnosis_without_exam_rejected(self, app_client_db):
        client, Session, term_id, class_id, _, student_id = app_client_db
        with Session() as s:
            agent_session = AgentSession(title="诊断", term_id=term_id, class_id=class_id, student_id=student_id, status="active")
            s.add(agent_session)
            s.commit()
            s.refresh(agent_session)
            sid = agent_session.id
        enabled_cfg = _enabled_config()
        with patch("backend.app.routers.agent.get_agent_config", return_value=enabled_cfg):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                resp = client.post(f"/api/v1/agent/sessions/{sid}/messages", json={"content": "诊断", "quick_task": "student_diagnosis"}, headers=AUTH_HEADERS)
        assert resp.status_code == 400
        assert resp.json()["detail"]["missing"] == ["exam_id"]

    def test_review_plan_without_exam_rejected(self, app_client_db):
        client, Session, term_id, _, _, _ = app_client_db
        with Session() as s:
            agent_session = AgentSession(title="复习", term_id=term_id, status="active")
            s.add(agent_session)
            s.commit()
            s.refresh(agent_session)
            sid = agent_session.id
        enabled_cfg = _enabled_config()
        with patch("backend.app.routers.agent.get_agent_config", return_value=enabled_cfg):
            with patch("backend.app.routers.agent.schedule_analysis_run", new_callable=AsyncMock):
                resp = client.post(f"/api/v1/agent/sessions/{sid}/messages", json={"content": "复习计划", "quick_task": "review_plan"}, headers=AUTH_HEADERS)
        assert resp.status_code == 400
        assert resp.json()["detail"]["missing"] == ["exam_id"]


class TestSessionReadFields:
    def test_session_read_has_scope_fields(self, app_client_db):
        client, _, term_id, class_id, exam_id, student_id = app_client_db
        resp = client.post("/api/v1/agent/sessions", json={"term_id": term_id, "class_id": class_id, "exam_id": exam_id, "student_id": student_id}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "class_id" in data
        assert "exam_id" in data
        assert "student_id" in data
        assert "scope_context" not in data
