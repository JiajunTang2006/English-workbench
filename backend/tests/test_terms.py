from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import AgentMessage, AgentSession, AnalysisEvidence, AnalysisRun, ExamPaperMemory


HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def state_with_student(name: str):
    return {
        "schema": 4,
        "teacher": {"name": "教师", "subject": "初中英语"},
        "classes": ["711"],
        "students": [{"id": "01", "name": name, "class": "711"}],
        "exams": [],
        "todos": [],
    }


def test_term_scoped_workspace_states_are_isolated(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    first = client.get("/api/v1/terms/current", headers=HEADERS).json()
    source_state = state_with_student("张三")
    source_state["archivedStudents"] = [{"id": "99", "name": "旧名单", "class": "711"}]
    saved = client.put(
        f"/api/v1/terms/{first['id']}/workspace-state",
        headers=HEADERS,
        json={"state": source_state, "expected_revision": 0},
    )
    assert saved.status_code == 200, saved.text

    second_response = client.post(
        "/api/v1/terms",
        headers=HEADERS,
        json={
            "code": "2026-S1",
            "name": "2026年第一学期",
            "starts_on": "2026-02-23",
            "ends_on": "2026-07-10",
            "clone_from_term_id": first["id"],
            "clone_classes": True,
            "clone_enrollments": True,
        },
    )
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    second_state = client.get(
        f"/api/v1/terms/{second['id']}/workspace-state", headers=HEADERS,
    ).json()
    assert second_state["state"]["students"][0]["name"] == "张三"
    assert second_state["state"]["exams"] == []
    assert second_state["state"]["archivedStudents"] == []

    updated = client.put(
        f"/api/v1/terms/{second['id']}/workspace-state",
        headers=HEADERS,
        json={"state": state_with_student("李四"), "expected_revision": second_state["revision"]},
    )
    assert updated.status_code == 200
    first_state = client.get(
        f"/api/v1/terms/{first['id']}/workspace-state", headers=HEADERS,
    ).json()
    assert first_state["state"]["students"][0]["name"] == "张三"


def test_classes_and_students_are_filtered_by_term(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    first = client.get("/api/v1/terms/current", headers=HEADERS).json()
    first_class = client.post(
        f"/api/v1/classes?term_id={first['id']}", headers=HEADERS, json={"name": "711"},
    ).json()
    client.post(
        f"/api/v1/students?term_id={first['id']}",
        headers=HEADERS,
        json={"student_no": "01", "name": "张三", "class_id": first_class["id"]},
    )
    second = client.post(
        "/api/v1/terms",
        headers=HEADERS,
        json={"code": "2026-S2", "name": "2026年第二学期"},
    ).json()
    assert client.get(f"/api/v1/classes?term_id={second['id']}", headers=HEADERS).json() == []
    assert client.get(f"/api/v1/students?term_id={second['id']}", headers=HEADERS).json() == []
    assert len(client.get(f"/api/v1/students?term_id={first['id']}", headers=HEADERS).json()) == 1


def test_student_enrollment_and_archive_are_isolated_by_term(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    first = client.get("/api/v1/terms/current", headers=HEADERS).json()
    first_class = client.post(
        f"/api/v1/classes?term_id={first['id']}", headers=HEADERS, json={"name": "711"},
    ).json()
    first_student = client.post(
        f"/api/v1/students?term_id={first['id']}", headers=HEADERS,
        json={"student_no": "01", "name": "张三", "class_id": first_class["id"], "target_score": 80},
    ).json()

    second = client.post(
        "/api/v1/terms", headers=HEADERS,
        json={"code": "2027-S1", "name": "2027年第一学期"},
    ).json()
    second_class = client.post(
        f"/api/v1/classes?term_id={second['id']}", headers=HEADERS, json={"name": "811"},
    ).json()
    second_student = client.post(
        f"/api/v1/students?term_id={second['id']}", headers=HEADERS,
        json={"student_no": "01", "name": "张三", "class_id": second_class["id"], "target_score": 95},
    )
    assert second_student.status_code == 201, second_student.text
    assert second_student.json()["id"] == first_student["id"]
    assert second_student.json()["class_id"] == second_class["id"]

    archived = client.post(
        f"/api/v1/students/{first_student['id']}/archive?term_id={second['id']}", headers=HEADERS,
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert client.get(f"/api/v1/students?term_id={second['id']}", headers=HEADERS).json() == []
    first_students = client.get(f"/api/v1/students?term_id={first['id']}", headers=HEADERS).json()
    assert first_students[0]["status"] == "active"
    assert first_students[0]["class_id"] == first_class["id"]
    assert first_students[0]["target_score"] == 80
    second_archived = client.get(
        f"/api/v1/students?term_id={second['id']}&include_archived=true", headers=HEADERS,
    ).json()
    assert second_archived[0]["target_score"] == 95


def test_exam_ids_cannot_cross_the_selected_term(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    first = client.get("/api/v1/terms/current", headers=HEADERS).json()
    exam = client.post(
        f"/api/v1/exams?term_id={first['id']}", headers=HEADERS,
        json={"name": "第一学期期中", "full_score": 100},
    ).json()
    second = client.post(
        "/api/v1/terms", headers=HEADERS,
        json={"code": "2027-S2", "name": "2027年第二学期"},
    ).json()
    client.put("/api/v1/terms/current", headers=HEADERS, json={"term_id": second["id"]})
    assert client.get(f"/api/v1/exams/{exam['id']}", headers=HEADERS).status_code == 404
    assert client.get(
        f"/api/v1/exams/{exam['id']}?term_id={first['id']}", headers=HEADERS,
    ).status_code == 200


def test_term_archive_is_recoverable_and_current_term_is_protected(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    first = client.get("/api/v1/terms/current", headers=HEADERS).json()
    second = client.post(
        "/api/v1/terms", headers=HEADERS,
        json={"code": "2028-S1", "name": "2028年第一学期"},
    ).json()
    assert client.post(f"/api/v1/terms/{first['id']}/archive", headers=HEADERS).status_code == 409
    archived = client.post(f"/api/v1/terms/{second['id']}/archive", headers=HEADERS)
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert all(item["id"] != second["id"] for item in client.get("/api/v1/terms", headers=HEADERS).json())
    restored = client.post(f"/api/v1/terms/{second['id']}/restore", headers=HEADERS)
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"


def test_permanent_delete_validates_before_backup_and_removes_attachments(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    first = client.get("/api/v1/terms/current", headers=HEADERS).json()
    second = client.post(
        "/api/v1/terms", headers=HEADERS, json={"code": "2029-S1", "name": "待删除学期"},
    ).json()
    content = b"term attachment"
    attachment = client.post(
        f"/api/v1/attachments?term_id={second['id']}",
        headers=HEADERS,
        json={
            "title": "原卷", "original_name": "paper.txt", "mime_type": "text/plain",
            "content_base64": __import__("base64").b64encode(content).decode(), "metadata": {},
        },
    ).json()
    storage_path = next((tmp_path / "attachments").glob("term_*"))

    wrong = client.request(
        "DELETE", f"/api/v1/terms/{second['id']}", headers=HEADERS,
        json={"confirmation_code": "wrong"},
    )
    assert wrong.status_code == 422
    assert list((tmp_path / "backups").iterdir()) == []

    deleted = client.request(
        "DELETE", f"/api/v1/terms/{second['id']}", headers=HEADERS,
        json={"confirmation_code": second["code"]},
    )
    assert deleted.status_code == 204, deleted.text
    assert not storage_path.exists()
    backup = next((tmp_path / "backups").glob(f"term_{second['id']}_before_delete_*"))
    assert (backup / "attachments" / storage_path.name).read_bytes() == content
    assert client.get(f"/api/v1/terms/{first['id']}/workspace-state", headers=HEADERS).status_code == 200


def test_term_cache_preview_and_clear_preserves_exam_facts(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    client = TestClient(app)
    term = client.get("/api/v1/terms/current", headers=HEADERS).json()
    exam = client.post(
        f"/api/v1/exams?term_id={term['id']}", headers=HEADERS,
        json={"name": "期中考试", "full_score": 100},
    ).json()
    db = app.state.session_factory()
    try:
        teachmate = AgentSession(term_id=term["id"], exam_id=exam["id"], title="考试分析")
        db.add(teachmate)
        db.flush()
        run = AnalysisRun(
            session_id=teachmate.id, term_id=term["id"], exam_id=exam["id"],
            capability="exam_analysis", status="completed",
        )
        db.add(run)
        db.flush()
        db.add_all([
            AgentMessage(session_id=teachmate.id, analysis_run_id=run.id,
                         role="user", content_text="分析这场考试"),
            ExamPaperMemory(exam_id=exam["id"], version=1, status="draft",
                            content_md="# AI 试卷记忆"),
            AnalysisEvidence(run_id=run.id, evidence_id="E-CACHE-1",
                             evidence_type="computed_metric"),
        ])
        db.commit()
    finally:
        db.close()

    preview = client.get(f"/api/v1/terms/{term['id']}/cache", headers=HEADERS)
    assert preview.status_code == 200, preview.text
    assert preview.json()["exam_paper_memories"] == 1
    assert preview.json()["sessions"] == 1
    assert preview.json()["analysis_runs"] == 1

    db = app.state.session_factory()
    try:
        db.query(AnalysisRun).update({AnalysisRun.status: "running"})
        db.commit()
    finally:
        db.close()
    blocked = client.post(
        f"/api/v1/terms/{term['id']}/cache/clear", headers=HEADERS,
        json={"confirm": True},
    )
    assert blocked.status_code == 409
    db = app.state.session_factory()
    try:
        db.query(AnalysisRun).update({AnalysisRun.status: "completed"})
        db.commit()
    finally:
        db.close()

    missing_confirmation = client.post(
        f"/api/v1/terms/{term['id']}/cache/clear", headers=HEADERS,
        json={"confirm": False},
    )
    assert missing_confirmation.status_code == 400

    cleared = client.post(
        f"/api/v1/terms/{term['id']}/cache/clear", headers=HEADERS,
        json={"confirm": True},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["static_knowledge_files_preserved"] is True
    assert cleared.json()["formal_attachments_preserved"] is True
    assert cleared.json()["teacher_confirmed_profiles_and_evaluations_preserved"] is True
    after = client.get(f"/api/v1/terms/{term['id']}/cache", headers=HEADERS).json()
    assert after["exam_paper_memories"] == 0
    assert after["sessions"] == 0
    assert after["analysis_runs"] == 0
    assert client.get(f"/api/v1/exams/{exam['id']}?term_id={term['id']}", headers=HEADERS).status_code == 200
