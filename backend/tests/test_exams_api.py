from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import ChangeLog, Exam, ExamClassMetric, ExamDimensionScore, ExamScore, ScoreDimension, WorkspaceState


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_exam_scores_summary_and_student_profile(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    classroom = client.post("/api/v1/classes", headers=headers, json={"name": "711"})
    assert classroom.status_code == 201
    class_id = classroom.json()["id"]
    students = []
    for index, (student_no, name) in enumerate((("01", "张三"), ("02", "李四"), ("03", "王五"))):
        response = client.post(
            "/api/v1/students",
            headers=headers,
            json={
                "student_no": student_no,
                "name": name,
                "class_id": class_id,
                "entrance_english": 180 if index == 0 else None,
                "target_score": 200 if index == 0 else None,
            },
        )
        assert response.status_code == 201
        students.append(response.json())
    assert students[0]["entrance_english"] == 180
    assert students[0]["target_score"] == 200

    exam_response = client.post(
        "/api/v1/exams",
        headers=headers,
        json={
            "name": "期中考试",
            "exam_date": "2026-11-10",
            "full_score": 150,
            "exam_type": "question_type",
            "dimensions": [
                {"code": "reading", "name": "阅读", "max_score": 60, "position": 1},
                {"code": "writing", "name": "写作", "max_score": 30, "position": 2},
            ],
        },
    )
    assert exam_response.status_code == 201, exam_response.text
    exam = exam_response.json()
    reading, writing = exam["dimensions"]

    scores_response = client.put(
        f"/api/v1/exams/{exam['id']}/scores",
        headers=headers,
        json={"items": [
            {
                "student_id": students[0]["id"], "total_score": 120,
                "grade_rank": 18,
                "dimension_scores": [
                    {"dimension_id": reading["id"], "score": 50},
                    {"dimension_id": writing["id"], "score": 25},
                ],
            },
            {"student_id": students[1]["id"], "total_score": 120, "grade_rank": 21},
            {"student_id": students[2]["id"], "attendance_status": "absent"},
        ]},
    )
    assert scores_response.status_code == 200, scores_response.text
    rows = scores_response.json()
    assert [row["rank"] for row in rows] == [1, 1, None]
    assert [row["class_rank"] for row in rows] == [1, 1, None]
    assert [row["grade_rank"] for row in rows] == [18, 21, None]

    detail = client.get(
        f"/api/v1/exams/{exam['id']}/students/{students[0]['id']}/score-details",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["total_score"] == 120
    assert detail.json()["detail_status"] == "no_paper"

    summary = client.get(f"/api/v1/exams/{exam['id']}/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json() == {
        "exam_id": exam["id"], "exam_name": "期中考试", "full_score": 150.0,
        "present_count": 2, "absent_count": 1, "average": 120.0,
        "highest": 120.0, "lowest": 120.0, "missing_score_count": 0,
    }

    profile = client.get(f"/api/v1/students/{students[0]['id']}/profile", headers=headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["latest_score"]["score_rate"] == 80.0
    assert profile.json()["latest_score"]["rank"] == 1
    assert profile.json()["latest_score"]["grade_rank"] == 18

    empty_metric = client.get(
        f"/api/v1/exams/{exam['id']}/class-metrics/{class_id}", headers=headers,
    )
    assert empty_metric.status_code == 200
    assert empty_metric.json()["grade_rank"] is None
    saved_metric = client.put(
        f"/api/v1/exams/{exam['id']}/class-metrics/{class_id}",
        headers=headers,
        json={"grade_rank": 3},
    )
    assert saved_metric.status_code == 200
    assert saved_metric.json() == {"exam_id": exam["id"], "class_id": class_id, "grade_rank": 3}
    assert client.put(
        f"/api/v1/exams/{exam['id']}/class-metrics/{class_id}",
        headers=headers,
        json={"grade_rank": 0},
    ).status_code == 422


def test_exam_archive_restore_purge_uses_workspace_revision_and_snapshot(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    exam = client.post("/api/v1/exams", headers=headers, json={"name": "可恢复考试", "source_key": "exam-local-1"})
    assert exam.status_code == 201, exam.text
    item = exam.json()
    state = {"exams": [{"id": "exam-local-1", "name": item["name"]}], "archivedExams": [], "currentExamId": "exam-local-1"}
    written = client.put("/api/v1/workspace-state", headers=headers, json={"state": state, "expected_revision": 0})
    assert written.status_code == 200, written.text
    revision = written.json()["revision"]

    archived = client.post(f"/api/v1/exams/{item['id']}/archive", headers=headers, json={"source_key": "exam-local-1", "expected_revision": revision})
    assert archived.status_code == 200, archived.text
    assert archived.json()["exam"]["status"] == "archived"
    assert archived.json()["state"]["exams"] == []
    assert archived.json()["state"]["archivedExams"][0]["id"] == "exam-local-1"

    with client.app.state.session_factory() as session:
        archive_log = session.scalar(select(ChangeLog).where(
            ChangeLog.entity == "exam",
            ChangeLog.entity_id == str(item["id"]),
            ChangeLog.action == "archive",
        ).order_by(ChangeLog.id.desc()))
        assert archive_log is not None
        assert archive_log.detail_json == {
            "term_id": 1,
            "source_key": "exam-local-1",
            "previous_revision": revision,
            "revision": revision + 1,
        }

    wrong_source = client.post(
        f"/api/v1/exams/{item['id']}/restore",
        headers=headers,
        json={"source_key": "other-exam", "expected_revision": archived.json()["revision"]},
    )
    assert wrong_source.status_code == 409
    wrong_term = client.post(
        f"/api/v1/exams/{item['id']}/restore",
        headers=headers,
        params={"term_id": 999},
        json={"source_key": "exam-local-1", "expected_revision": archived.json()["revision"]},
    )
    assert wrong_term.status_code == 404
    with client.app.state.session_factory() as session:
        stored_exam = session.get(Exam, item["id"])
        workspace = session.get(WorkspaceState, 1)
        assert stored_exam.status == "archived"
        assert workspace.revision == archived.json()["revision"]
        assert workspace.state_json["archivedExams"][0]["id"] == "exam-local-1"

    conflict = client.post(f"/api/v1/exams/{item['id']}/restore", headers=headers, json={"source_key": "exam-local-1", "expected_revision": revision})
    assert conflict.status_code == 409
    restored = client.post(f"/api/v1/exams/{item['id']}/restore", headers=headers, json={"source_key": "exam-local-1", "expected_revision": archived.json()["revision"]})
    assert restored.status_code == 200, restored.text
    purged = client.post(f"/api/v1/exams/{item['id']}/purge", headers=headers, json={"source_key": "exam-local-1", "expected_revision": restored.json()["revision"]})
    assert purged.status_code == 409
    archived_again = client.post(f"/api/v1/exams/{item['id']}/archive", headers=headers, json={"source_key": "exam-local-1", "expected_revision": restored.json()["revision"]})
    assert archived_again.status_code == 200
    purged = client.post(f"/api/v1/exams/{item['id']}/purge", headers=headers, json={"source_key": "exam-local-1", "expected_revision": archived_again.json()["revision"]})
    assert purged.status_code == 200, purged.text
    assert purged.json()["exam"] is None
    assert client.get(f"/api/v1/exams/{item['id']}", headers=headers, params={"include_archived": "true"}).status_code == 404


def test_exam_purge_cascades_scores_dimensions_metrics_and_logs_change(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    classroom = client.post("/api/v1/classes", headers=headers, json={"name": "测试班"}).json()
    student = client.post(
        "/api/v1/students",
        headers=headers,
        json={"student_no": "01", "name": "测试生", "class_id": classroom["id"]},
    ).json()
    exam = client.post(
        "/api/v1/exams",
        headers=headers,
        json={
            "name": "待清理考试",
            "source_key": "purge-local-1",
            "dimensions": [{"code": "reading", "name": "阅读", "max_score": 50, "position": 1}],
        },
    ).json()
    saved_scores = client.put(
        f"/api/v1/exams/{exam['id']}/scores",
        headers=headers,
        json={"items": [{
            "student_id": student["id"],
            "total_score": 40,
            "dimension_scores": [{"dimension_id": exam["dimensions"][0]["id"], "score": 35}],
        }]},
    )
    assert saved_scores.status_code == 200, saved_scores.text
    metric = client.put(
        f"/api/v1/exams/{exam['id']}/class-metrics/{classroom['id']}",
        headers=headers,
        json={"grade_rank": 2},
    )
    assert metric.status_code == 200, metric.text
    state = {
        "exams": [{"id": "purge-local-1", "name": exam["name"]}],
        "archivedExams": [],
        "currentExamId": "purge-local-1",
    }
    workspace = client.put(
        "/api/v1/workspace-state",
        headers=headers,
        json={"state": state, "expected_revision": 0},
    ).json()
    archived = client.post(
        f"/api/v1/exams/{exam['id']}/archive",
        headers=headers,
        json={"source_key": "purge-local-1", "expected_revision": workspace["revision"]},
    ).json()
    purged = client.post(
        f"/api/v1/exams/{exam['id']}/purge",
        headers=headers,
        json={"source_key": "purge-local-1", "expected_revision": archived["revision"]},
    )
    assert purged.status_code == 200, purged.text
    assert purged.json()["state"]["archivedExams"] == []

    with client.app.state.session_factory() as session:
        assert session.get(Exam, exam["id"]) is None
        for model in (ScoreDimension, ExamScore, ExamDimensionScore, ExamClassMetric):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        purge_log = session.scalar(select(ChangeLog).where(
            ChangeLog.entity == "exam",
            ChangeLog.entity_id == str(exam["id"]),
            ChangeLog.action == "purge",
        ))
        assert purge_log is not None
        assert purge_log.detail_json["previous_revision"] == archived["revision"]
        assert purge_log.detail_json["revision"] == archived["revision"] + 1
        assert purge_log.detail_json["source_key"] == "purge-local-1"


def test_all_classes_keep_their_own_standard_competition_rank(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    class_ids = [
        client.post("/api/v1/classes", headers=headers, json={"name": name}).json()["id"]
        for name in ("甲班", "乙班")
    ]
    students = []
    for class_index, class_id in enumerate(class_ids):
        for index in range(4):
            students.append(client.post(
                "/api/v1/students",
                headers=headers,
                json={"student_no": f"{class_index}{index}", "name": f"学生{class_index}{index}", "class_id": class_id},
            ).json())
    exam_id = client.post(
        "/api/v1/exams", headers=headers, json={"name": "联考", "full_score": 100},
    ).json()["id"]
    values = [98, 95, 95, 90, 100, 80, 70, 60]
    saved = client.put(
        f"/api/v1/exams/{exam_id}/scores",
        headers=headers,
        json={"items": [
            {"student_id": student["id"], "total_score": score}
            for student, score in zip(students, values)
        ]},
    )
    assert saved.status_code == 200, saved.text
    rows = saved.json()
    first_class = [row["class_rank"] for row in rows if row["class_id"] == class_ids[0]]
    second_class = [row["class_rank"] for row in rows if row["class_id"] == class_ids[1]]
    assert first_class == [1, 2, 2, 4]
    assert second_class == [1, 2, 3, 4]


def test_score_validation_rejects_impossible_values(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    class_id = client.post("/api/v1/classes", headers=headers, json={"name": "712"}).json()["id"]
    student_id = client.post(
        "/api/v1/students", headers=headers,
        json={"student_no": "01", "name": "测试生", "class_id": class_id},
    ).json()["id"]
    exam_id = client.post(
        "/api/v1/exams", headers=headers,
        json={"name": "周测", "full_score": 100},
    ).json()["id"]

    too_high = client.put(
        f"/api/v1/exams/{exam_id}/scores", headers=headers,
        json={"items": [{"student_id": student_id, "total_score": 101}]},
    )
    assert too_high.status_code == 422
    absent_with_score = client.put(
        f"/api/v1/exams/{exam_id}/scores", headers=headers,
        json={"items": [{"student_id": student_id, "attendance_status": "absent", "total_score": 50}]},
    )
    assert absent_with_score.status_code == 422


def test_core_invariants_reject_invalid_threshold_and_nonempty_class_archive(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    classroom = client.post("/api/v1/classes", headers=headers, json={"name": "713"}).json()
    client.post(
        "/api/v1/students", headers=headers,
        json={"student_no": "01", "name": "测试生", "class_id": classroom["id"]},
    )
    archive = client.post(f"/api/v1/classes/{classroom['id']}/archive", headers=headers)
    assert archive.status_code == 409
    invalid_settings = client.patch(
        "/api/v1/settings", headers=headers,
        json={"excellent_line": 60, "passing_line": 70},
    )
    assert invalid_settings.status_code == 422
    blank_name = client.patch(
        f"/api/v1/classes/{classroom['id']}", headers=headers,
        json={"name": "   "},
    )
    assert blank_name.status_code == 422
