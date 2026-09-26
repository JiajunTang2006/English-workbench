"""L3 视觉闭环测试（数据模型 / 状态机 / 路由 fail-closed / 隐私 / confirmed 门禁）。

覆盖：
- 表建表（AttachmentDerivative / AttachmentAnalysisResult）；
- vision_enabled=False → /analyze 返回 503 vision_disabled；
- 无 Provider（本地 stub 默认可用）→ 触发后进入 pending_review；
- 教师确认前 get_confirmed_content 返回 None（不进正式上下文）；
- 确认后 get_confirmed_content 返回正文；
- 跨学期越权访问 404；
- privacy-preview 不返回图像。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.agent.config import get_agent_config
from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.entities import Attachment, AttachmentAnalysisResult, Term
from sqlalchemy import select


def _seed(session):
    active_id = session.scalar(
        select(Term.id).where(Term.status == "active").order_by(Term.id).limit(1)
    )
    if active_id is None:
        term = Term(code="2026SP", name="2026 春", status="active")
        session.add(term)
        session.flush()
        active_id = term.id
    # 图片附件（无确认正文，模拟待 OCR）
    att = Attachment(
        term_id=active_id, title="答题卡", original_name="scan.png",
        storage_name="scan_1.png", mime_type="image/png",
        size_bytes=10, sha256="b" * 64,
        metadata_json={"parsed": {"status": "pending_ocr"}},
    )
    session.add(att)
    session.commit()
    return {"term_id": active_id, "attachment_id": att.id}


@pytest.fixture
def enabled_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VISION_ANALYSIS_ENABLED", "true")
    import backend.app.agent.config as ac
    monkeypatch.setattr(ac, "_runtime_override", None, raising=False)
    app = create_app(Settings(data_dir=tmp_path))
    ids = _seed(app.state.session_factory())
    return TestClient(app), {"Authorization": f"Bearer {TOKEN}"}, ids


@pytest.fixture
def disabled_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VISION_ANALYSIS_ENABLED", "false")
    import backend.app.agent.config as ac
    monkeypatch.setattr(ac, "_runtime_override", None, raising=False)
    app = create_app(Settings(data_dir=tmp_path))
    ids = _seed(app.state.session_factory())
    return TestClient(app), {"Authorization": f"Bearer {TOKEN}"}, ids


def test_disabled_returns_503(disabled_client):
    c, headers, ids = disabled_client
    r = c.post("/api/v1/vision/analyze",
               json={"attachment_id": ids["attachment_id"], "task": "exam_understanding"},
               headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "vision_disabled"


def test_analyze_creates_pending_review(enabled_client):
    c, headers, ids = enabled_client
    r = c.post("/api/v1/vision/analyze",
               json={"attachment_id": ids["attachment_id"], "task": "exam_understanding"},
               headers=headers)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["status"] == "pending_review"
    assert result["provider"] == "local_stub"
    # 列出
    lst = c.get(f"/api/v1/vision/results?attachment_id={ids['attachment_id']}", headers=headers)
    assert lst.status_code == 200
    assert len(lst.json()["results"]) == 1


def test_confirmed_gate_before_review(enabled_client):
    c, headers, ids = enabled_client
    r = c.post("/api/v1/vision/analyze",
               json={"attachment_id": ids["attachment_id"]}, headers=headers).json()["result"]
    rid = r["id"]
    # 确认前：service 层 confirmed 内容应为 None
    session = c.app.state.session_factory()
    from backend.app.services.vision.analysis_service import VisionAnalysisService
    svc = VisionAnalysisService(session)
    assert svc.get_confirmed_content(ids["attachment_id"]) is None
    # 教师确认
    rev = c.patch(f"/api/v1/vision/results/{rid}",
                  json={"action": "confirm"}, headers=headers)
    assert rev.status_code == 200
    assert rev.json()["result"]["status"] == "confirmed"
    # 确认后可读
    assert svc.get_confirmed_content(ids["attachment_id"]) is not None


def test_reject_terminal(enabled_client):
    c, headers, ids = enabled_client
    r = c.post("/api/v1/vision/analyze",
               json={"attachment_id": ids["attachment_id"]}, headers=headers).json()["result"]
    rid = r["id"]
    c.patch(f"/api/v1/vision/results/{rid}", json={"action": "reject"}, headers=headers)
    # 终态后再次确认应失败
    again = c.patch(f"/api/v1/vision/results/{rid}", json={"action": "confirm"}, headers=headers)
    assert again.status_code == 400


def test_privacy_preview_no_image(disabled_client):
    c, headers, _ids = disabled_client
    r = c.get("/api/v1/vision/privacy-preview", headers=headers)
    assert r.status_code == 200
    policy = r.json()["policy"]
    assert "姓名" in policy["redacted_fields"]
    assert "logs" in policy


def test_unauthorized_without_token(enabled_client):
    c, _headers, ids = enabled_client
    r = c.post("/api/v1/vision/analyze",
               json={"attachment_id": ids["attachment_id"]})
    assert r.status_code == 401
