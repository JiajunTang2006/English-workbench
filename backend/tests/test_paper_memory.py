"""试卷记忆（记忆板块）测试：生成/确认/分类检测/分析包注入/API"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.agent.analysis_packet import build_packet, packet_to_text
from backend.app.agent.providers.base import ModelResponse
from backend.app.agent.providers.fake_text import FakeTextProvider
from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.database import Base
from backend.app.factory import create_app
from backend.app.models import (
    AnalysisEvidence, AnalysisRun, Class, Exam, ExamPaperMemory,
    ExamPaperVersion, ExamQuestion, ExamScore, Student, StudentItemResult, Term,
)
from backend.app.services import paper_memory

MEMORY_MD = (
    "## 卷面结构\n完形 5 分、阅读 10 分、语法填空 5 分。\n"
    "## 考点与课标要求\n固定搭配对应三级词汇要求。\n"
    "## 易错预设\n完形第 1 题预设动词搭配失分。\n"
    "## 复习钩子\n完形逻辑复盘（任务 B）。"
)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/memory.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture()
def seed(db):
    term = Term(code="T1", name="学期")
    db.add(term)
    db.flush()
    cls = Class(term_id=term.id, name="初三（1）班")
    db.add(cls)
    db.flush()
    students = [Student(student_no=f"S{i}", name=n, class_id=cls.id)
                for i, n in enumerate(("张三", "李四"), start=1)]
    db.add_all(students)
    db.flush()
    exam = Exam(term_id=term.id, name="期中考试", full_score=20,
                source_key="pm:mid")
    db.add(exam)
    db.flush()
    for student, total in zip(students, (15.0, 9.0)):
        db.add(ExamScore(exam_id=exam.id, student_id=student.id,
                         total_score=total, class_id_at_exam=cls.id,
                         attendance_status="present"))
    run = AnalysisRun(session_id=1, term_id=term.id, capability="exam_analysis",
                      status="running")
    db.add(run)
    paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed",
                             full_score=20)
    db.add(paper)
    db.flush()
    q1 = ExamQuestion(paper_version_id=paper.id, question_no="1",
                      question_type="完形填空", max_score=10,
                      knowledge_nodes_json=["固定搭配", "量子纠缠式阅读"])
    q2 = ExamQuestion(paper_version_id=paper.id, question_no="2",
                      question_type="阅读理解", max_score=10,
                      knowledge_nodes_json=["细节定位"])
    db.add_all([q1, q2])
    db.flush()
    for student, scores in zip(students, ([4.0, 8.0], [3.0, 6.0])):
        for question, score in zip((q1, q2), scores):
            db.add(StudentItemResult(
                exam_id=exam.id, student_id=student.id, question_id=question.id,
                score=score, correct=score >= question.max_score,
                attendance_status="present"))
    db.commit()
    return {"term": term, "cls": cls, "students": students, "exam": exam,
            "run": run, "q1": q1, "q2": q2}


def _fake_provider():
    return FakeTextProvider(steps=[ModelResponse(content=MEMORY_MD)])


class TestClassification:

    def test_known_and_unknown_split(self):
        result = paper_memory.classify_knowledge_points(
            ["宾语从句", "细节定位", "量子纠缠式阅读", ""])
        assert "宾语从句" in result["known"]
        assert "细节定位" in result["known"]
        assert result["unknown"] == ["量子纠缠式阅读"]


class TestGenerateAndConfirm:

    def test_generate_creates_draft_with_gaps(self, db, seed):
        result = paper_memory.generate_paper_memory(
            db, seed["exam"].id, provider=_fake_provider())
        assert result["ok"] is True
        assert result["status"] == "draft"
        assert result["version"] == 1
        assert MEMORY_MD.splitlines()[0] in result["content_md"]
        # 未入分类表的知识点进 gaps
        assert "量子纠缠式阅读" in result["knowledge_gaps"]
        assert "固定搭配" not in result["knowledge_gaps"]

    def test_regenerate_supersedes_previous(self, db, seed):
        paper_memory.generate_paper_memory(db, seed["exam"].id,
                                           provider=_fake_provider())
        second = paper_memory.generate_paper_memory(
            db, seed["exam"].id, provider=_fake_provider())
        assert second["version"] == 2 and second["status"] == "draft"
        first = db.scalar(select(ExamPaperMemory).where(
            ExamPaperMemory.version == 1))
        assert first.status == "superseded"

    def test_confirm_flow_and_reconfirm_supersedes(self, db, seed):
        first = paper_memory.generate_paper_memory(db, seed["exam"].id,
                                                   provider=_fake_provider())
        confirmed = paper_memory.confirm_paper_memory(
            db, seed["exam"].id, first["id"], confirmed_by="teacher")
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmed_at"]

        second = paper_memory.generate_paper_memory(db, seed["exam"].id,
                                                    provider=_fake_provider())
        reconfirmed = paper_memory.confirm_paper_memory(
            db, seed["exam"].id, second["id"], content="## 手工修订版")
        assert reconfirmed["status"] == "confirmed"
        assert reconfirmed["source"] == "manual"
        old = db.get(ExamPaperMemory, first["id"])
        assert old.status == "superseded"

    def test_new_draft_keeps_confirmed_memory_active(self, db, seed):
        first = paper_memory.generate_paper_memory(
            db, seed["exam"].id, provider=_fake_provider())
        paper_memory.confirm_paper_memory(db, seed["exam"].id, first["id"])

        second = paper_memory.generate_paper_memory(
            db, seed["exam"].id, provider=_fake_provider())

        assert second["status"] == "draft"
        old = db.get(ExamPaperMemory, first["id"])
        assert old.status == "confirmed"
        content, version = paper_memory.get_confirmed_content(
            db, seed["exam"].id)
        assert version == 1 and content == old.content_md

    def test_get_confirmed_content(self, db, seed):
        assert paper_memory.get_confirmed_content(db, seed["exam"].id) is None
        first = paper_memory.generate_paper_memory(db, seed["exam"].id,
                                                   provider=_fake_provider())
        paper_memory.confirm_paper_memory(db, seed["exam"].id, first["id"])
        content, version = paper_memory.get_confirmed_content(
            db, seed["exam"].id)
        assert version == 1 and "卷面结构" in content


class TestPacketInjection:

    def _packet(self, db, seed):
        scope = {"run_id": seed["run"].id, "term_id": seed["term"].id,
                 "class_id": seed["cls"].id, "exam_id": seed["exam"].id,
                 "student_id": None}
        return build_packet(db, "exam_analysis", scope)

    def test_without_memory_notes_limitation(self, db, seed):
        packet = self._packet(db, seed)
        assert packet["paper_memory"] is None
        assert any("尚未建立试卷记忆" in text for text in packet["limitations"])
        assert "【试卷记忆" not in packet_to_text(packet)

    def test_with_memory_injects_section_and_evidence(self, db, seed):
        first = paper_memory.generate_paper_memory(db, seed["exam"].id,
                                                   provider=_fake_provider())
        paper_memory.confirm_paper_memory(db, seed["exam"].id, first["id"])
        packet = self._packet(db, seed)
        assert packet["paper_memory"]["version"] == 1
        assert "卷面结构" in packet["paper_memory"]["content"]
        text = packet_to_text(packet)
        assert "【试卷记忆 v1】" in text
        assert not any("尚未建立试卷记忆" in t for t in packet["limitations"])
        evidence_id = packet["paper_memory"]["evidence_id"]
        assert evidence_id in text
        row = db.scalar(select(AnalysisEvidence).where(
            AnalysisEvidence.evidence_id == evidence_id))
        assert row is not None
        assert row.evidence_type == "paper_memory"

    def test_review_packet_carries_memory(self, db, seed):
        first = paper_memory.generate_paper_memory(db, seed["exam"].id,
                                                   provider=_fake_provider())
        paper_memory.confirm_paper_memory(db, seed["exam"].id, first["id"])
        packet = build_packet(db, "review_plan", {
            "run_id": seed["run"].id, "term_id": seed["term"].id,
            "class_id": seed["cls"].id, "exam_id": seed["exam"].id,
            "student_id": None})
        assert packet["paper_memory"] is not None


class TestPaperMemoryAPI:

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paper_memory, "_build_provider", _fake_provider)
        app = create_app(Settings(data_dir=tmp_path))
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {TOKEN}"})
            yield client, tmp_path

    def test_generate_confirm_get_roundtrip(self, client):
        client, tmp_path = client
        with client as active:
            session = active.app.state.session_factory()
            try:
                term = Term(code="TA", name="学期A")
                session.add(term)
                session.flush()
                exam = Exam(term_id=term.id, name="月考", full_score=20,
                            source_key="api:mid")
                session.add(exam)
                session.flush()
                paper = ExamPaperVersion(exam_id=exam.id, version=1,
                                         status="confirmed", full_score=20)
                session.add(paper)
                session.flush()
                session.add(ExamQuestion(paper_version_id=paper.id,
                                         question_no="1", question_type="完形填空",
                                         max_score=10,
                                         knowledge_nodes_json=["固定搭配"]))
                session.commit()
                exam_id, term_id = exam.id, term.id
            finally:
                session.close()

        empty = client.get(
            f"/api/v1/exams/{exam_id}/paper-memory?term_id={term_id}"
        ).json()
        assert empty["data"] is None

        generated = client.post(
            f"/api/v1/exams/{exam_id}/paper-memory/generate"
            f"?term_id={term_id}").json()
        assert generated["ok"] is True, generated
        assert generated["status"] == "draft"

        confirmed = client.post(
            f"/api/v1/exams/{exam_id}/paper-memory/"
            f"{generated['id']}/confirm?term_id={term_id}",
            json={"content_md": "## 教师修订版"}).json()
        assert confirmed["ok"] is True
        assert confirmed["source"] == "manual"

        final = client.get(
            f"/api/v1/exams/{exam_id}/paper-memory?term_id={term_id}"
        ).json()
        assert final["data"]["status"] == "confirmed"
        assert "教师修订版" in final["data"]["content_md"]

    def test_generate_without_structure_returns_404(self, client):
        client, _ = client
        with client as active:
            session = active.app.state.session_factory()
            try:
                term = Term(code="TB", name="学期B")
                session.add(term)
                session.flush()
                exam = Exam(term_id=term.id, name="空考试", full_score=100,
                            source_key="api:empty")
                session.add(exam)
                session.commit()
                exam_id = exam.id
            finally:
                session.close()
        response = client.post(f"/api/v1/exams/{exam_id}/paper-memory/generate")
        assert response.status_code == 404

    def test_read_and_confirm_reject_cross_term_access(self, client):
        client, _ = client
        with client as active:
            session = active.app.state.session_factory()
            try:
                term_a = Term(code="TC", name="学期C")
                term_b = Term(code="TD", name="学期D")
                session.add_all([term_a, term_b])
                session.flush()
                exam = Exam(term_id=term_a.id, name="跨学期测试", full_score=20,
                            source_key="api:cross-term")
                session.add(exam)
                session.flush()
                paper = ExamPaperVersion(exam_id=exam.id, version=1,
                                         status="confirmed", full_score=20)
                session.add(paper)
                session.flush()
                session.add(ExamQuestion(
                    paper_version_id=paper.id, question_no="1",
                    question_type="完形填空", max_score=20,
                    knowledge_nodes_json=["固定搭配"]))
                session.commit()
                exam_id, own_term_id, other_term_id = (
                    exam.id, term_a.id, term_b.id)
            finally:
                session.close()

        generated = client.post(
            f"/api/v1/exams/{exam_id}/paper-memory/generate"
            f"?term_id={own_term_id}").json()
        memory_id = generated["id"]

        assert client.get(
            f"/api/v1/exams/{exam_id}/paper-memory?term_id={other_term_id}"
        ).status_code == 404
        assert client.post(
            f"/api/v1/exams/{exam_id}/paper-memory/{memory_id}/confirm"
            f"?term_id={other_term_id}"
        ).status_code == 404
