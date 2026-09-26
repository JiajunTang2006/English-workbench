from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app


HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def sample_state(student_name: str = "张三"):
    return {
        "schema": 1,
        "teacher": {"name": "冯老师", "subject": "初中英语"},
        "classes": ["711"],
        "students": [{"id": "01", "name": student_name, "class": "711"}],
        "exams": [],
        "todos": [],
    }


def test_workspace_state_round_trip_and_revision_conflict(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))

    empty = client.get("/api/v1/workspace-state", headers=HEADERS)
    assert empty.status_code == 200
    assert empty.json() == {"state": None, "revision": 0, "updated_at": None}

    created = client.put(
        "/api/v1/workspace-state",
        headers=HEADERS,
        json={"state": sample_state(), "expected_revision": 0},
    )
    assert created.status_code == 200, created.text
    assert created.json()["revision"] == 1

    conflict = client.put(
        "/api/v1/workspace-state",
        headers=HEADERS,
        json={"state": sample_state("李四"), "expected_revision": 0},
    )
    assert conflict.status_code == 409

    updated = client.put(
        "/api/v1/workspace-state",
        headers=HEADERS,
        json={"state": sample_state("李四"), "expected_revision": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    normalized_students = client.get("/api/v1/students", headers=HEADERS).json()
    assert normalized_students[0]["name"] == "李四"
    assert client.get("/api/v1/classes", headers=HEADERS).json()[0]["name"] == "711"

    reloaded_client = TestClient(create_app(Settings(data_dir=tmp_path)))
    saved = reloaded_client.get("/api/v1/workspace-state", headers=HEADERS).json()
    assert saved["revision"] == 2
    assert saved["state"]["students"][0]["name"] == "李四"


def test_workspace_state_requires_local_token(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    assert client.get("/api/v1/workspace-state").status_code == 401


def test_workspace_backup_is_created_before_destructive_replace(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    client.put(
        "/api/v1/workspace-state",
        headers=HEADERS,
        json={"state": sample_state(), "expected_revision": 0},
    )
    response = client.post("/api/v1/workspace-state/backup", headers=HEADERS)
    assert response.status_code == 200, response.text
    backup = tmp_path / "backups" / response.json()["name"]
    assert (backup / "workbench.db").is_file()
    assert (backup / "manifest.json").is_file()
    assert len(response.json()["checksum"]) == 64


def test_database_workbench_keeps_original_interface(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    response = client.get("/workbench")
    assert response.status_code == 200
    assert "workbench-core.js" in response.text
    assert "workbench-views.js" in response.text
    assert "workbench-interactions.js" in response.text
    assert client.get("/workbench-assets/workbench-core.js").status_code == 200
