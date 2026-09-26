from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.database import create_session_factory
from backend.app.factory import create_app
from backend.app.models import AppSetting, Class, Enrollment, Exam, ExamPaperVersion, ExamQuestion, ExamScore, ExternalEntityMapping, Student, StudentItemResult, Term, WorkspaceState
import backend.app.services.moni_sync as moni_sync


def test_mock_school_sync_preview_apply_and_idempotency(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}

    payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
    preview = client.post("/api/v1/school-sync/preview", headers=headers, json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["errors"] == []
    assert preview.json()["counts"]["item_scores"] == 8

    first = client.post("/api/v1/school-sync/apply", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"

    # 逐题证据链可读，并允许教师在保留原始同步来源的前提下修正。
    with client as active_client:
        session = active_client.app.state.session_factory()
        try:
            exam_record = session.scalar(select(Exam))
            exam_id = exam_record.id
            term_id = exam_record.term_id
            student_id = session.scalar(select(Student.id).order_by(Student.id))
        finally:
            session.close()
    item_rows = client.get(
        f"/api/v1/exams/{exam_id}/students/{student_id}/item-results?term_id={term_id}",
        headers=headers,
    )
    assert item_rows.status_code == 200, item_rows.text
    assert len(item_rows.json()) == 4
    first_item = item_rows.json()[0]
    corrected = client.patch(
        f"/api/v1/exams/{exam_id}/students/{student_id}/item-results/{first_item['question_id']}?term_id={term_id}",
        headers=headers,
        json={"score": first_item["max_score"], "override_note": "教师复核"},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["teacher_override"] is True
    assert corrected.json()["override_note"] == "教师复核"
    metrics = client.get(f"/api/v1/exams/{exam_id}/question-metrics?term_id={term_id}", headers=headers)
    assert metrics.status_code == 200, metrics.text
    assert len(metrics.json()["questions"]) == 4
    assert metrics.json()["questions"][0]["participant_count"] == 2

    second = client.post("/api/v1/school-sync/apply", headers=headers, json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["counts"]["conflicts"] >= 1

    with client as active_client:
        session = active_client.app.state.session_factory()
        try:
            assert session.scalar(select(func.count(ExamScore.id))) == 2
            assert session.scalar(select(func.count(ExamPaperVersion.id))) == 1
            assert session.scalar(select(func.count(ExamQuestion.id))) == 4
            assert session.scalar(select(func.count(StudentItemResult.id))) == 8
        finally:
            session.close()

    runs = client.get("/api/v1/school-sync/runs", headers=headers)
    assert runs.status_code == 200
    assert len(runs.json()) == 2


def test_school_sync_rejects_item_score_over_question_max(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
    payload["students"][0]["item_scores"][0]["score"] = 99
    response = client.post("/api/v1/school-sync/apply", headers=headers, json=payload)
    assert response.status_code == 422
    assert "超过满分" in response.text


def test_school_sync_does_not_turn_missing_total_into_zero(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
        for student in payload["students"]:
            student["total_score"] = None
            student["item_scores"] = []
        response = client.post("/api/v1/school-sync/apply", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                assert all(score.total_score is None for score in session.scalars(select(ExamScore)).all())
            finally:
                session.close()


def test_school_sync_incomplete_batch_keeps_existing_item_scores(tmp_path):
    """回归（评审 P1）：不完整批次不得把已有小分/总分覆盖为空。

    第二批里第一名学生不再携带逐题成绩（接口本次没取到），到场状态不变。
    旧实现会把该生已有小分和总分写成 None，丢失事实。
    """
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
        first = client.post("/api/v1/school-sync/apply", headers=headers, json=payload)
        assert first.status_code == 200, first.text

        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                first_student_no = payload["students"][0]["student_no"]
                student = session.scalar(select(Student).where(Student.student_no == first_student_no))
                before_items = session.scalar(
                    select(func.count()).select_from(StudentItemResult).where(
                        StudentItemResult.student_id == student.id,
                        StudentItemResult.score.is_not(None),
                    )
                )
                before_score = session.scalar(
                    select(ExamScore).where(ExamScore.student_id == student.id)
                ).total_score
                assert before_items and before_items > 0
                assert before_score is not None
                student_id = student.id
            finally:
                session.close()

        # 第二批：该生不再带逐题成绩，也没有单科总分（但仍在场）。
        payload["students"][0]["item_scores"] = []
        payload["students"][0]["total_score"] = None
        second = client.post("/api/v1/school-sync/apply", headers=headers, json=payload)
        assert second.status_code == 200, second.text

        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                after_items = session.scalar(
                    select(func.count()).select_from(StudentItemResult).where(
                        StudentItemResult.student_id == student_id,
                        StudentItemResult.score.is_not(None),
                    )
                )
                after_score = session.scalar(
                    select(ExamScore).where(ExamScore.student_id == student_id)
                ).total_score
                assert after_items == before_items, "不完整批次把已有小分覆盖为空"
                assert after_score == before_score, "不完整批次把已有总分覆盖为空"
            finally:
                session.close()


def test_school_sync_persists_auto_tier_lines_to_exam_and_legacy_snapshot(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
        payload["exam"].update({"tier_a_cutoff": 99.5, "tier_b_cutoff": 88.5, "tier_c_cutoff": 71.5})
        response = client.post("/api/v1/school-sync/apply", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                exam = session.scalar(select(Exam))
                workspace = session.scalar(select(WorkspaceState))
                assert (exam.tier_a_cutoff, exam.tier_b_cutoff, exam.tier_c_cutoff) == (99.5, 88.5, 71.5)
                snapshot_exam = workspace.state_json["exams"][0]
                assert snapshot_exam["tierLines"] == {"a": 99.5, "b": 88.5, "c": 71.5}
            finally:
                session.close()


def test_school_sync_restores_tiers_and_grade_ranks_after_workspace_reset(tmp_path):
    """重置旧前端快照后，再同步必须由代码完整重建考试分析字段。"""
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
        payload["exam"].update({"tier_a_cutoff": 90, "tier_b_cutoff": 75, "tier_c_cutoff": 60})
        payload["students"][0]["grade_rank"] = 1
        payload["students"][1]["grade_rank"] = 2
        assert client.post("/api/v1/school-sync/apply", headers=headers, json=payload).status_code == 200
        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                term_id = session.scalar(select(Term.id))
            finally:
                session.close()
        current = client.get(f"/api/v1/terms/{term_id}/workspace-state", headers=headers).json()
        reset = client.put(
            f"/api/v1/terms/{term_id}/workspace-state",
            headers=headers,
            json={"expected_revision": current["revision"], "state": {"classes": [], "students": [], "exams": []}},
        )
        assert reset.status_code == 200, reset.text
        assert client.post("/api/v1/school-sync/apply", headers=headers, json=payload).status_code == 200
        restored = client.get(f"/api/v1/terms/{term_id}/workspace-state", headers=headers).json()["state"]
        assert restored["exams"][0]["tierLines"] == {"a": 90.0, "b": 75.0, "c": 60.0}
        assert {row["gradeRank"] for row in restored["exams"][0]["scores"].values()} == {1, 2}


def test_school_sync_accepts_changed_paper_shape_as_new_version(tmp_path):
    """不同场次/改版题数不固定：旧版本保留，新版本独立生效。"""
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
        assert client.post("/api/v1/school-sync/apply", headers=headers, json=payload).status_code == 200
        changed = json.loads(json.dumps(payload))
        changed["snapshot_id"] = "mock-2026-08-24-shape-50"
        changed["exam"]["full_score"] = 101
        changed["questions"].append({"external_id": "q-5", "question_no": "5", "sub_question_no": "a", "max_score": 1})
        response = client.post("/api/v1/school-sync/apply", headers=headers, json=changed)
        assert response.status_code == 200, response.text
        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                assert session.scalar(select(func.count(ExamPaperVersion.id))) == 2
                assert session.scalar(select(func.count(ExamQuestion.id))) == 9
            finally:
                session.close()


def test_school_sync_background_job_reports_progress_and_last_success(tmp_path):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        payload = client.get("/api/v1/school-sync/mock/payload", headers=headers).json()
        queued = client.post("/api/v1/school-sync/jobs", headers=headers, json=payload)
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["job_id"]

        seen_phases = set()
        final = None
        for _ in range(100):
            current = client.get(f"/api/v1/school-sync/jobs/{job_id}", headers=headers)
            assert current.status_code == 200, current.text
            body = current.json()
            if body.get("phase"):
                seen_phases.add(body["phase"])
            if body["status"] in {"completed", "failed", "cancelled"}:
                final = body
                break
            time.sleep(0.05)

        assert final is not None
        assert final["status"] == "completed"
        assert final["progress"] == 1.0
        assert final["status_label"] == "已完成，有数据提示"
        assert final["processed"] == 2
        phase_history = final["checkpoint"].get("phase_history", [])
        observed_phases = seen_phases | {
            item.get("phase") for item in phase_history if isinstance(item, dict)
        }
        assert "validating" in observed_phases or "writing" in observed_phases

        status = client.get("/api/v1/school-sync/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["last_success_at"] is not None
        assert status.json()["last_success_run_id"] == final["checkpoint"]["run_id"]


def test_moni_roster_sync_is_idempotent_and_visible_to_core_api(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        payload = {
            "source_key": "moni",
            "source_name": "MONI 学生数据",
            "snapshot_id": "moni-snapshot-1",
            "term": {"external_id": "2026-fall", "code": "2026-fall", "name": "2026 秋季学期"},
            "classes": [{"external_id": "c-1", "name": "九年级1班", "grade": "九年级", "school_year": "2026"}],
            "students": [
                {"external_id": "s-1", "student_no": "20260001", "name": "测试甲", "class_external_id": "c-1"},
                {"external_id": "s-2", "student_no": "20260002", "name": "测试乙", "class_external_id": "c-1"},
            ],
        }
        first = client.post("/api/v1/school-sync/roster/apply", headers=headers, json=payload)
        second = client.post("/api/v1/school-sync/roster/apply", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                term_id = session.scalar(select(Term.id))
            finally:
                session.close()
        students_response = client.get(f"/api/v1/students?term_id={term_id}", headers=headers)
        assert students_response.status_code == 200, students_response.text
        students = students_response.json()
        assert {row["student_no"] for row in students} == {"20260001", "20260002"}
        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                assert session.scalar(select(func.count(Student.id))) == 2
                assert session.scalar(select(func.count(Class.id))) == 1
                assert session.scalar(select(func.count(Enrollment.id))) == 2
            finally:
                session.close()


def test_moni_config_saves_key_in_the_active_app_data_dir(tmp_path, monkeypatch):
    """MONI 配置必须跟随当前应用实例的数据目录，而不是全局默认目录。"""
    default_dir = tmp_path / "default-data-dir"
    app_dir = tmp_path / "active-app-data-dir"
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(default_dir))

    with TestClient(create_app(Settings(data_dir=app_dir))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        response = client.put(
            "/api/v1/school-sync/moni/config",
            headers=headers,
            json={
                "mcpServers": {
                    "moni": {
                        "type": "http",
                        "url": "https://t-mcp.fufenxi.com/api/t-mcp/mcp",
                        "headers": {"Authorization": "Bearer test-moni-key"},
                    }
                }
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["token_configured"] is True
        assert response.json()["key_hint"].endswith("-key")

    from backend.app.agent.keyvault import load_api_key

    assert load_api_key("moni", data_dir=app_dir) == "test-moni-key"
    assert load_api_key("moni", data_dir=default_dir) is None


def test_moni_sync_entrypoint_activates_imported_term(tmp_path, monkeypatch):
    class FakeManager:
        def __init__(self, _data_dir):
            pass

    class FakeReader:
        def __init__(self, _manager):
            pass

        def read(self, path):
            assert path == "/school/current-term.json"
            return [{"termId": "term-2027", "termCode": "2027-spring", "termName": "2027 春季学期"}]

        def query(self, path):
            if path == "/classes/.list.jsonl":
                return [{"classId": "class-1", "className": "九年级1班"}]
            if path == "/classes/class-1/students/.list.jsonl":
                return [{"studentId": "student-1", "studentNo": "20270001", "name": "测试甲"}]
            return []

    monkeypatch.setattr(moni_sync, "PluginManager", FakeManager)
    monkeypatch.setattr(moni_sync, "MoniReader", FakeReader)
    monkeypatch.setattr(moni_sync, "_discover_class_root", lambda _reader: "/classes")

    summary = moni_sync.sync_moni_roster(Settings(data_dir=tmp_path))
    assert summary["completed"] is True
    with create_session_factory(Settings(data_dir=tmp_path).database_url)() as session:
        term = session.scalar(select(Term).where(Term.code == "2027-spring"))
        setting = session.get(AppSetting, "active_term_id")
        assert term is not None
        assert setting.value_json == term.id


def test_moni_roster_creates_term_scoped_enrollments_and_reuses_student_profile(tmp_path):
    """跨学期时保留历史在班关系，但同一学生只保留一份全局档案。"""
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        base = {
            "source_key": "moni",
            "source_name": "MONI 学生数据",
            "classes": [{"external_id": "c-fall", "name": "九年级1班", "grade": "九年级", "school_year": "2026"}],
            "students": [
                {"external_id": "s-1", "student_no": "20260001", "name": "测试甲", "class_external_id": "c-fall"},
                {"external_id": "s-2", "student_no": "20260002", "name": "测试乙", "class_external_id": "c-fall"},
            ],
        }
        fall = {**base, "snapshot_id": "moni-fall", "term": {"external_id": "2026-fall", "code": "2026-fall", "name": "2026 秋季学期"}}
        spring = {
            **base,
            "snapshot_id": "moni-spring",
            "term": {"external_id": "2027-spring", "code": "2027-spring", "name": "2027 春季学期"},
            "classes": [{"external_id": "c-spring", "name": "九年级2班", "grade": "九年级", "school_year": "2027"}],
            "students": [
                {"external_id": "s-1", "student_no": "20260001", "name": "测试甲", "class_external_id": "c-spring"},
                {"external_id": "s-3", "student_no": "20270003", "name": "测试丙", "class_external_id": "c-spring"},
            ],
        }
        assert client.post("/api/v1/school-sync/roster/apply", headers=headers, json=fall).status_code == 200
        assert client.post("/api/v1/school-sync/roster/apply", headers=headers, json=spring).status_code == 200

        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                terms = {term.code: term.id for term in session.scalars(select(Term)).all()}
                students = session.scalars(select(Student)).all()
                enrollments = session.scalars(select(Enrollment).order_by(Enrollment.term_id, Enrollment.student_id)).all()
                assert {code for code in terms if code != "initial"} == {"2026-fall", "2027-spring"}
                assert len(students) == 3
                assert len(enrollments) == 4
                assert {item.term_id for item in enrollments} == {terms["2026-fall"], terms["2027-spring"]}
                assert session.scalar(select(func.count(ExternalEntityMapping.id)).where(ExternalEntityMapping.entity_type == "class")) == 2
            finally:
                session.close()

        fall_students = client.get(f"/api/v1/students?term_id={terms['2026-fall']}", headers=headers).json()
        spring_students = client.get(f"/api/v1/students?term_id={terms['2027-spring']}", headers=headers).json()
        assert {row["student_no"] for row in fall_students} == {"20260001", "20260002"}
        assert {row["student_no"] for row in spring_students} == {"20260001", "20270003"}


def test_moni_roster_archives_removed_class_and_students_in_current_term(tmp_path):
    """同一学期换班/换名单时，当前名单收敛，旧记录进入归档而非被删除。"""
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = {
            "source_key": "moni",
            "source_name": "MONI 学生数据",
            "snapshot_id": "moni-roster-a",
            "term": {"external_id": "2026-fall", "code": "2026-fall", "name": "2026 秋季学期"},
            "classes": [{"external_id": "c-a", "name": "九年级1班"}],
            "students": [
                {"external_id": "s-1", "student_no": "20260001", "name": "测试甲", "class_external_id": "c-a"},
                {"external_id": "s-2", "student_no": "20260002", "name": "测试乙", "class_external_id": "c-a"},
            ],
        }
        second = {
            **first,
            "snapshot_id": "moni-roster-b",
            "classes": [{"external_id": "c-b", "name": "九年级2班"}],
            "students": [
                {"external_id": "s-1", "student_no": "20260001", "name": "测试甲", "class_external_id": "c-b"},
                {"external_id": "s-3", "student_no": "20260003", "name": "测试丙", "class_external_id": "c-b"},
            ],
        }
        assert client.post("/api/v1/school-sync/roster/apply", headers=headers, json=first).status_code == 200
        updated = client.post("/api/v1/school-sync/roster/apply", headers=headers, json=second)
        assert updated.status_code == 200, updated.text
        assert updated.json()["counts"]["archived_students"] == 1
        assert updated.json()["counts"]["archived_classes"] == 1

        with client as active_client:
            session = active_client.app.state.session_factory()
            try:
                term_id = session.scalar(select(Term.id))
                classes = {item.name: item for item in session.scalars(select(Class)).all()}
                enrollments = session.scalars(select(Enrollment).order_by(Enrollment.student_id)).all()
                assert classes["九年级1班"].status == "archived"
                assert classes["九年级2班"].status == "active"
                assert {item.status for item in enrollments if item.student.student_no == "20260002"} == {"archived"}
                moved = next(item for item in enrollments if item.student.student_no == "20260001")
                assert moved.classroom.name == "九年级2班"
                workspace = session.scalar(select(WorkspaceState))
                assert {item["id"] for item in workspace.state_json["students"]} == {"20260001", "20260003"}
                assert {item["id"] for item in workspace.state_json["archivedStudents"]} == {"20260002"}
                assert workspace.state_json["classes"] == ["九年级2班"]
            finally:
                session.close()

        current = client.get(f"/api/v1/students?term_id={term_id}", headers=headers).json()
        historical = client.get(f"/api/v1/students?term_id={term_id}&include_archived=true", headers=headers).json()
        assert {row["student_no"] for row in current} == {"20260001", "20260003"}
        assert {row["student_no"] for row in historical} == {"20260001", "20260002", "20260003"}
