from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.factory import create_app


def test_root_redirects_to_workbench(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/workbench"


def test_database_workbench_uses_only_local_runtime_assets(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    response = client.get("/workbench")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "")
    assert "cdn.jsdelivr.net" not in response.text
    assert "fonts.googleapis.com" not in response.text
    for asset in (
        "/workbench-assets/xlsx.full.min.js",
        "/workbench-assets/jszip.min.js",
        "/workbench-assets/echarts.min.js",
        "/workbench-assets/material-symbols-rounded.css",
        "/workbench-assets/material-symbols-rounded.ttf",
        "/workbench-assets/teachmate-icon.jpg",
        "/workbench-assets/illustrations/teachmate-doodle-teacher.svg",
        "/workbench-assets/illustrations/teachmate-doodle-books.svg",
        "/workbench-assets/illustrations/teachmate-doodle-underline.svg",
    ):
        assert client.get(asset).status_code == 200
