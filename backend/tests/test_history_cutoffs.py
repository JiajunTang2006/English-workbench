from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app


def test_exam_cutoffs_and_class_snapshot_survive_transfer(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first = client.post("/api/v1/classes", headers=headers, json={"name": "甲班"}).json()
    second = client.post("/api/v1/classes", headers=headers, json={"name": "乙班"}).json()
    student = client.post(
        "/api/v1/students", headers=headers,
        json={"student_no": "A001", "name": "测试生", "class_id": first["id"]},
    ).json()
    exam = client.post(
        "/api/v1/exams", headers=headers,
        json={"name": "入学考试", "exam_kind": "entrance", "full_score": 100,
              "tier_a_cutoff": 90, "tier_b_cutoff": 80, "tier_c_cutoff": 60},
    ).json()
    assert exam["exam_kind"] == "entrance"
    saved = client.put(
        f"/api/v1/exams/{exam['id']}/scores", headers=headers,
        json={"items": [{"student_id": student["id"], "total_score": 85, "grade_rank": 12}]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()[0]["tier"] == "B"
    moved = client.patch(f"/api/v1/students/{student['id']}", headers=headers, json={"class_id": second["id"]})
    assert moved.status_code == 200
    rows = client.get(f"/api/v1/exams/{exam['id']}/scores", headers=headers).json()
    assert rows[0]["class_id"] == first["id"]
    profile = client.get(f"/api/v1/students/{student['id']}/profile", headers=headers).json()
    assert profile["exams"][0]["class_name"] == "甲班"
    assert profile["exams"][0]["tier"] == "B"
