from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import Student
from backend.tests.test_workspace_state import sample_state
from tools.import_student_snapshot import main


@pytest.mark.parametrize(
    ("gender_entry", "expected_gender"),
    [
        ({}, "女"),
        ({"gender": "男"}, "男"),
        ({"gender": None}, None),
    ],
    ids=["missing-preserves-existing", "explicit-value-updates", "explicit-null-clears"],
)
def test_snapshot_import_only_changes_explicit_gender(
    tmp_path, monkeypatch, gender_entry, expected_gender,
):
    settings = Settings(data_dir=tmp_path)
    client = TestClient(create_app(settings))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    state = sample_state("导入测试学生")
    state["students"][0]["english"] = 80
    created = client.put(
        "/api/v1/workspace-state",
        headers=headers,
        json={"state": state, "expected_revision": 0},
    )
    assert created.status_code == 200, created.text

    with client.app.state.session_factory() as session:
        student = session.scalar(select(Student).where(Student.student_no == "01"))
        student.gender = "女"
        session.commit()

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({
        "total": 1,
        "students": [{
            "student_no": "01",
            "name": "导入测试学生",
            "entrance_english": 80,
            **gender_entry,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "import_student_snapshot", str(snapshot), "--data-dir", str(tmp_path), "--apply",
    ])

    main()

    with client.app.state.session_factory() as session:
        student = session.scalar(select(Student).where(Student.student_no == "01"))
        assert student.gender == expected_gender
