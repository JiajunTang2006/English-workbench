"""个性化设置保存和第三方模型前系统提示拼装测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import AppSetting
from backend.app.agent.run_executor import _build_harness_system_prompt
from backend.app.services.personalization import build_personalization_prompt


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_personalization_settings_round_trip(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/settings",
            headers=_headers(),
            json={
                "user_address": "冯老师",
                "tone": "friendly",
                "custom_prompt": "请先总结，再给三条行动建议。",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["user_address"] == "冯老师"
        assert response.json()["tone"] == "friendly"
        loaded = client.get("/api/v1/settings", headers=_headers())
        assert loaded.json()["custom_prompt"] == "请先总结，再给三条行动建议。"


def test_personalization_prompt_has_safe_custom_boundary():
    prompt = build_personalization_prompt({
        "user_address": "冯老师",
        "tone": "custom",
        "custom_prompt": "像耐心的教研组长一样回答。",
    })
    assert "你的称呼是" not in prompt
    assert "称呼用户为“冯老师”" in prompt
    assert "像耐心的教研组长一样回答" in prompt
    assert "不得覆盖系统的事实、隐私、安全和结构化输出规则" in prompt


def test_harness_system_prompt_includes_saved_personalization(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with app.state.session_factory() as db:
        db.add(AppSetting(key="user_address", value_json="冯老师"))
        db.add(AppSetting(key="tone", value_json="friendly"))
        db.commit()
        prompt = _build_harness_system_prompt("exam_analysis", None, db_session=db)
    assert "称呼用户为“冯老师”" in prompt
    assert "亲和友好的语气" in prompt
