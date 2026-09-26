"""L2 Codex 插件 API 测试（TeachMatePluginAPI v1）。

覆盖：禁用 503、未认证 401、配对→使用→撤销、scope、跨学期泄漏防护、
快照/档案/复习事实/确认资料/证据 DTO 形状与确定性。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.agent.config import get_agent_config
from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.agent_entities import (
    AgentMessage, AgentSession, AnalysisEvidence, AnalysisRun,
    ErrorCauseAssessment, ExamPaperVersion, ExamQuestion, StudentItemResult,
)
from backend.app.models.entities import (
    Attachment, Class, Enrollment, Exam, ExamDimensionScore, ExamScore,
    ScoreDimension, Student, Term,
)
from backend.app.services.plugin_auth import PluginTokenStore


def _seed(session):
    from sqlalchemy import select

    from backend.app.models.entities import Term as _Term

    # 复用应用启动已创建的当前学期，避免越学期（current_term_id 解析到默认学期）
    active_id = session.scalar(
        select(_Term.id).where(_Term.status == "active").order_by(_Term.id).limit(1)
    )
    if active_id is None:
        term = _Term(code="2026SP", name="2026 春", status="active")
        session.add(term)
        session.flush()
        active_id = term.id
    term_id = active_id
    classroom = Class(term_id=term_id, name="初三(1)班", status="active")
    session.add(classroom)
    session.flush()
    exam = Exam(term_id=term_id, name="摸底考", full_score=120, exam_type="english_total",
                exam_kind="regular", status="active", exam_date=None)
    session.add(exam)
    session.flush()
    dim = ScoreDimension(exam_id=exam.id, code="reading", name="阅读", max_score=60, position=0)
    dim2 = ScoreDimension(exam_id=exam.id, code="writing", name="写作", max_score=60, position=1)
    session.add_all([dim, dim2])
    session.flush()
    stu = Student(student_no="S001", name="张三")
    session.add(stu)
    session.flush()
    enr = Enrollment(term_id=term_id, class_id=classroom.id, student_id=stu.id,
                     status="active", weak_tags="时态,冠词")
    session.add(enr)
    session.flush()
    score = ExamScore(exam_id=exam.id, student_id=stu.id, total_score=96,
                      attendance_status="present", class_id_at_exam=classroom.id)
    session.add(score)
    session.flush()
    session.add(ExamDimensionScore(exam_score_id=score.id, dimension_id=dim.id, score=20))
    session.add(ExamDimensionScore(exam_score_id=score.id, dimension_id=dim2.id, score=50))
    paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed", full_score=120)
    session.add(paper)
    session.flush()
    question = ExamQuestion(
        paper_version_id=paper.id, question_no="3", question_type="语法填空",
        content_text="Yesterday he ___ to school.", options_json={}, max_score=5,
        correct_answer_json={"answer": "went"}, knowledge_nodes_json=["一般过去时"],
        pitfall_tags_json=["不规则动词"],
    )
    session.add(question)
    session.flush()
    item = StudentItemResult(
        exam_id=exam.id, student_id=stu.id, question_id=question.id,
        score=2, score_rate=0.4, correct=False, selected_option="go",
        student_answer_text="go", attendance_status="present",
    )
    session.add(item)
    session.flush()
    session.add(ErrorCauseAssessment(
        exam_id=exam.id, student_id=stu.id, question_id=question.id,
        cause="语法", confidence=0.92, source="teacher", status="confirmed",
    ))
    # 已确认资料（文件需存在；确认状态在 metadata_json.parsed.status）
    att = Attachment(term_id=term_id, title="考点梳理", original_name="考点.pdf",
                     storage_name="mat_1.txt", mime_type="application/pdf",
                     size_bytes=10, sha256="a" * 64,
                     metadata_json={"parsed": {"status": "confirmed",
                                                "content": "这是已确认的正文内容。",
                                                "confirmed_at": "2026-08-22T00:00:00+00:00"}})
    session.add(att)
    # 已完成分析及其结构化报告（供新 MCP 报告接口读取）
    agent_session = AgentSession(title="摸底考分析", term_id=term_id,
                                 class_id=classroom.id, exam_id=exam.id)
    session.add(agent_session)
    session.flush()
    now = datetime.now(timezone.utc)
    run = AnalysisRun(capability="exam_analysis", term_id=term_id,
                      class_id=classroom.id, exam_id=exam.id,
                      session_id=agent_session.id, status="completed",
                      started_at=now, completed_at=now,
                      runtime_kind="harness", runtime_version="test")
    session.add(run)
    session.flush()
    from backend.app.services.agent_analysis.report_snapshot import ensure_report_scope_snapshot
    ensure_report_scope_snapshot(session, run)
    report = {
        "answer_type": "exam_analysis",
        "summary": "本次考试整体表现稳定，张三需要关注阅读维度。",
        "findings": [{"title": "阅读维度需要关注", "description": "张三在阅读部分得分偏低。"}],
        "recommendations": [{"title": "安排针对性练习", "description": "建议为张三安排阅读专项练习。"}],
        "limitations": [],
        "evidence_ids": ["ev-1"],
        "schema_version": "1.0.0",
    }
    session.add(AgentMessage(
        session_id=agent_session.id, analysis_run_id=run.id, role="assistant",
        content_text="已完成考试分析。", structured_answer_json=report,
        evidence_ids_json=["ev-1"], created_at=now,
    ))
    # 证据（依赖 AnalysisRun）
    ev = AnalysisEvidence(run_id=run.id, evidence_id="ev-1", evidence_type="stat",
                          display_summary="平均分 96", local_fact_json={"average": 96},
                          source_entity="exam_scores", source_field="total_score",
                          numerator=96, denominator=1, calculation_formula="avg")
    session.add(ev)
    session.commit()
    return {"term_id": term_id, "class_id": classroom.id, "exam_id": exam.id,
            "student_id": stu.id, "material_id": att.id, "evidence_id": "ev-1"}


@pytest.fixture
def disabled_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CODEX_PLUGIN_ENABLED", "false")
    import backend.app.agent.config as ac
    monkeypatch.setattr(ac, "_runtime_override", None, raising=False)
    app = create_app(Settings(data_dir=tmp_path))
    return TestClient(app), {"Authorization": f"Bearer {TOKEN}"}, tmp_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CODEX_PLUGIN_ENABLED", "true")
    import backend.app.agent.config as ac
    monkeypatch.setattr(ac, "_runtime_override", None, raising=False)
    app = create_app(Settings(data_dir=tmp_path))
    with app.state.session_factory() as s:
        ids = _seed(s)
    (tmp_path / "attachments").mkdir(exist_ok=True)
    (tmp_path / "attachments" / "mat_1.txt").write_text("file exists", encoding="utf-8")
    return TestClient(app), {"Authorization": f"Bearer {TOKEN}"}, tmp_path, ids


def _plugin_token(c, headers, tmp_path):
    """走配对流程拿到插件令牌。"""
    store = PluginTokenStore(tmp_path / "plugin")
    code = store.create_pairing_code()
    resp = c.post("/api/v1/plugin/auth/pair", headers=headers, json={"code": code})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


class TestPluginDisabled:
    def test_status_returns_503_when_disabled(self, disabled_client):
        c, headers, _ = disabled_client
        # 无插件令牌也应直接 503（开关优先）
        resp = c.get("/api/v1/plugin/status")
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"]["code"] == "plugin_disabled"


class TestPluginAuth:
    def test_unauthenticated_without_token(self, client):
        c, _h, _t, _ids = client
        resp = c.get("/api/v1/plugin/scopes")
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "unauthenticated"

    def test_pair_use_revoke_flow(self, client):
        c, headers, tmp_path, _ids = client
        token = _plugin_token(c, headers, tmp_path)
        # 工具可用
        resp = c.get("/api/v1/plugin/status",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        assert "get_exam_snapshot" in resp.json()["capabilities"]
        # 撤销后失效
        listing = c.get("/api/v1/plugin/auth/tokens", headers=headers).json()
        assert listing and listing[0]["scope"] == "teaching.read"
        tid = listing[0]["id"]
        rev = c.post("/api/v1/plugin/auth/revoke", headers=headers, json={"token_id": tid})
        assert rev.status_code == 200
        after = c.get("/api/v1/plugin/status",
                      headers={"Authorization": f"Bearer {token}"})
        assert after.status_code == 401

    def test_invalid_pairing_code(self, client):
        c, headers, _t, _ids = client
        resp = c.post("/api/v1/plugin/auth/pair", headers=headers, json={"code": "nope"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "invalid_pairing_code"


class TestPluginTools:
    def test_one_click_workbuddy_connect_returns_stdio_config(self, client):
        c, headers, _tmp_path, _ids = client
        resp = c.post("/api/v1/plugin/auth/connect", headers=headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        server = body["config"]["mcpServers"]["teachmate"]
        assert body["scope"] == "teaching.read"
        assert server["type"] == "stdio"
        assert server["args"] == ["-m", "server"]
        assert server["env"]["TEACHMATE_PLUGIN_TOKEN"] == body["token"]

    def test_scopes(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        resp = c.get("/api/v1/plugin/scopes",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["term_id"] == ids["term_id"]
        assert any(e["id"] == ids["exam_id"] for e in body["exams"])

    def test_exam_snapshot_dto(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        resp = c.get(f"/api/v1/plugin/exams/{ids['exam_id']}/snapshot",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["exam"]["exam_name"] == "摸底考"
        assert body["exam"]["present_count"] == 1
        assert body["data_quality"]["missing_scores"] == 0

    def test_exam_snapshot_scopes_to_selected_class(self, client):
        c, _h, tmp_path, ids = client
        # 在同一场考试增加另一个班级；查询原班级时不得把该学生混入统计。
        with c.app.state.session_factory() as session:
            other_class = Class(term_id=ids["term_id"], name="初三(2)班", status="active")
            session.add(other_class)
            session.flush()
            other_student = Student(student_no="S002", name="李四")
            session.add(other_student)
            session.flush()
            session.add(Enrollment(term_id=ids["term_id"], class_id=other_class.id,
                                   student_id=other_student.id, status="active"))
            session.add(ExamScore(exam_id=ids["exam_id"], student_id=other_student.id,
                                  total_score=60, attendance_status="present",
                                  class_id_at_exam=other_class.id))
            session.commit()
            other_class_id = other_class.id
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            f"/api/v1/plugin/exams/{ids['exam_id']}/snapshot"
            f"?term_id={ids['term_id']}&class_id={ids['class_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["class_id"] == ids["class_id"]
        assert resp.json()["exam"]["present_count"] == 1
        other = c.get(
            f"/api/v1/plugin/exams/{ids['exam_id']}/snapshot"
            f"?term_id={ids['term_id']}&class_id={other_class_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert other.status_code == 200, other.text
        assert other.json()["exam"]["average"] == 60

    def test_latest_analysis_report_is_scoped_and_anonymized_by_default(self, client):
        c, _h, tmp_path, ids = client
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            "/api/v1/plugin/analysis-reports/latest"
            f"?term_id={ids['term_id']}&class_id={ids['class_id']}&exam_id={ids['exam_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scope"]["class_id"] == ids["class_id"]
        assert body["scope"]["exam_id"] == ids["exam_id"]
        assert body["run"]["status"] == "completed"
        assert body["report"]["answer_type"] == "exam_analysis"
        assert "张三" not in body["report"]["summary"]
        assert "student_" in body["report"]["summary"]
        assert body["evidence_ids"] == ["ev-1"]
        assert body["snapshot_source"] == "frozen"
        assert body["scope_snapshot"]["data_quality"]["scored_count"] == 1

        identified = c.get(
            "/api/v1/plugin/analysis-reports/latest"
            f"?term_id={ids['term_id']}&class_id={ids['class_id']}&exam_id={ids['exam_id']}&identify=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert identified.status_code == 200, identified.text
        assert "张三" in identified.json()["report"]["summary"]

    def test_analysis_report_catalog_discovers_real_reports(self, client):
        c, _h, tmp_path, ids = client
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            "/api/v1/plugin/analysis-reports"
            f"?term_id={ids['term_id']}&class_id={ids['class_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["reports"][0]["scope"]["exam_id"] == ids["exam_id"]
        assert body["reports"][0]["run"]["run_id"] > 0
        assert body["reports"][0]["snapshot_source"] == "frozen"

    def test_report_data_quality_uses_frozen_snapshot(self, client):
        c, _h, tmp_path, ids = client
        with c.app.state.session_factory() as session:
            row = session.scalar(select(ExamScore).where(ExamScore.exam_id == ids["exam_id"]))
            row.total_score = None
            session.commit()
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            "/api/v1/plugin/analysis-reports/latest"
            f"?term_id={ids['term_id']}&class_id={ids['class_id']}&exam_id={ids['exam_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data_quality"] == {
            "present_count": 1, "scored_count": 1,
            "absent_count": 0, "missing_scores": 0,
        }

    def test_latest_analysis_report_rejects_cross_term_class(self, client):
        c, _h, tmp_path, ids = client
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            "/api/v1/plugin/analysis-reports/latest"
            f"?term_id={ids['term_id']}&class_id=999999&exam_id={ids['exam_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "not_found"

    def test_student_profile_anonymized(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        resp = c.get(f"/api/v1/plugin/students/{ids['student_id']}/profile",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["identifiable"] is False
        assert body["student_anon_id"]
        assert "name" not in body or body.get("name") is None

    def test_search_students_is_scoped_and_anonymized_by_default(self, client):
        c, _h, tmp_path, ids = client
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            "/api/v1/plugin/students/search"
            f"?term_id={ids['term_id']}&class_id={ids['class_id']}&keyword=张",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["students"][0]["display_name"] is None
        assert body["students"][0]["student_anon_id"]

        identified = c.get(
            "/api/v1/plugin/students/search"
            f"?term_id={ids['term_id']}&keyword=S001&identify=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert identified.status_code == 200, identified.text
        assert identified.json()["students"][0]["display_name"] == "张三"

    def test_student_practice_context_contains_profile_wrong_item_and_cause(self, client):
        c, _h, tmp_path, ids = client
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            f"/api/v1/plugin/students/{ids['student_id']}/practice-context"
            f"?term_id={ids['term_id']}&exam_id={ids['exam_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["student_anon_id"]
        assert body["total_wrong_items"] == 1
        assert body["wrong_items"][0]["content_text"] == "Yesterday he ___ to school."
        assert body["wrong_items"][0]["knowledge_points"] == ["一般过去时"]
        assert body["wrong_items"][0]["error_causes"][0]["cause"] == "语法"
        assert body["error_patterns"][0]["confirmed_count"] == 1

    def test_review_plan_facts(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        resp = c.get(f"/api/v1/plugin/review-plans/facts?exam_id={ids['exam_id']}",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert any(d["dimension_name"] == "写作" for d in body["dimension_averages"])
        assert "时态" in body["weak_knowledge_points"]

    def test_review_plan_facts_strictly_scopes_class(self, client):
        c, _h, tmp_path, ids = client
        with c.app.state.session_factory() as session:
            other_class = Class(term_id=ids["term_id"], name="初三(2)班", status="active")
            session.add(other_class)
            session.flush()
            other_student = Student(student_no="S009", name="其他班学生")
            session.add(other_student)
            session.flush()
            session.add(Enrollment(
                term_id=ids["term_id"], class_id=other_class.id,
                student_id=other_student.id, status="active", weak_tags="其他班薄弱项",
            ))
            session.add(ExamScore(
                exam_id=ids["exam_id"], student_id=other_student.id,
                total_score=40, attendance_status="present",
                class_id_at_exam=other_class.id,
            ))
            session.commit()
        token = _plugin_token(c, _h, tmp_path)
        resp = c.get(
            "/api/v1/plugin/review-plans/facts"
            f"?exam_id={ids['exam_id']}&term_id={ids['term_id']}&class_id={ids['class_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["coverage"]["present_count"] == 1
        assert body["coverage"]["class_count"] == 1
        assert "时态" in body["weak_knowledge_points"]
        assert "其他班薄弱项" not in body["weak_knowledge_points"]

    def test_formal_materials_only_confirmed(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        resp = c.get("/api/v1/plugin/materials",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 1
        assert body[0]["material_id"] == ids["material_id"]
        # 读取分页
        r2 = c.get(f"/api/v1/plugin/materials/{ids['material_id']}?page=1&page_size=4000",
                   headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 200, r2.text
        assert "已确认的正文" in r2.json()["text"]

    def test_evidence_view(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        resp = c.get(f"/api/v1/plugin/evidence/{ids['evidence_id']}",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["evidence_id"] == ids["evidence_id"]
        assert body["source"]["entity"] == "exam_scores"

    def test_cross_term_leak_rejected(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        # 用错误 term_id 访问考试 → 404（FastAPI 侧复核归属）
        resp = c.get(f"/api/v1/plugin/exams/{ids['exam_id']}/snapshot?term_id=999999",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

    def test_api_version_mismatch(self, client):
        c, _h, _t, ids = client
        token = _plugin_token(c, _h, _t)
        resp = c.get(f"/api/v1/plugin/exams/{ids['exam_id']}/snapshot?api_version=9.9",
                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "api_version_unsupported"
