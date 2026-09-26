"""B3-07 附件正式资料闭环测试

覆盖：
1. parse_failed 附件不得晋升为 confirmed（409）；
2. 文件丢失的附件不得晋升（409）；
3. 正常晋升成功且幂等（重复晋升 200）；
4. 晋升后正式资料进入 FormalContextProvider 可引用集；
5. 正式资料注入上限（max_chars / max_attachments）生效。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import get_global_session_factory
from backend.app.models.entities import Attachment
from backend.app.models.agent_entities import AgentMessage, AgentMessageAttachment


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    from backend.app.factory import create_app

    monkeypatch.setenv("WORKBENCH_TOKEN", "b3-test-token")
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as c:
        yield c, tmp_path


@pytest.fixture
def headers():
    from backend.app.auth import TOKEN
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def base(app_env):
    from backend.app.models.entities import Term
    from backend.app.models.agent_entities import AgentSession

    with get_global_session_factory()() as db:
        t = Term(code="t1", name="2026春",
                 starts_on=date(2026, 1, 1), ends_on=date(2026, 7, 1),
                 status="active")
        db.add(t)
        db.commit()
        s = AgentSession(title="S1", term_id=t.id, status="active")
        db.add(s)
        db.commit()
        return {"tmp": app_env[1], "term_id": t.id, "session_id": s.id}


def _mk_attachment(base, *, status="pending_review", error=None, content="正式正文内容",
                   storage_name="ok.txt", present_file=True) -> int:
    data_dir = base["tmp"]
    att_dir = data_dir / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    if present_file:
        (att_dir / storage_name).write_text(content, encoding="utf-8")
    with get_global_session_factory()() as db:
        att = Attachment(term_id=base["term_id"], title="附件",
                         original_name=storage_name, mime_type="text/plain",
                         size_bytes=10, sha256="0" * 64,
                         storage_name=storage_name,
                         metadata_json={"parsed": {
                             "status": status, "content": content,
                             "format": "text", "error": error,
                         }})
        db.add(att)
        db.commit()
        att_id = att.id
        msg = AgentMessage(session_id=base["session_id"], role="user",
                           content_text="hi")
        db.add(msg)
        db.commit()
        db.add(AgentMessageAttachment(message_id=msg.id, attachment_id=att_id,
                                      purpose="exam_paper"))
        db.commit()
        return att_id


def _promote(c, base, aid, headers):
    return c.post(
        f"/api/v1/agent/attachments/{aid}/promote",
        headers=headers,
        json={"attachment_id": aid, "session_id": base["session_id"],
              "purpose": "exam_paper"},
    )


class TestPromoteGate:

    def test_parse_failed_rejected(self, app_env, headers, base):
        c, _ = app_env
        aid = _mk_attachment(base, status="parse_failed", error="OCR 失败")
        resp = _promote(c, base, aid, headers)
        assert resp.status_code == 409
        assert "解析失败" in resp.json()["detail"]

    def test_missing_file_rejected(self, app_env, headers, base):
        c, _ = app_env
        aid = _mk_attachment(base, status="pending_review", present_file=False)
        resp = _promote(c, base, aid, headers)
        assert resp.status_code == 409
        assert "丢失" in resp.json()["detail"]

    def test_success_and_idempotent(self, app_env, headers, base):
        c, _ = app_env
        aid = _mk_attachment(base, status="pending_review")
        assert _promote(c, base, aid, headers).status_code == 200
        assert _promote(c, base, aid, headers).status_code == 200

    def test_injected_after_promote(self, app_env, headers, base):
        c, _ = app_env
        aid = _mk_attachment(base, status="pending_review", content="正式正文XYZ")
        assert _promote(c, base, aid, headers).status_code == 200
        from backend.app.services.agent_analysis.formal_context import (
            FormalContextProvider,
        )
        with get_global_session_factory()() as db:
            provider = FormalContextProvider(db, data_dir=base["tmp"])
            text = provider.get_formal_context_text(base["session_id"])
            assert text is not None
            assert "正式正文XYZ" in text

    def test_limits_apply(self, app_env, headers, base):
        c, _ = app_env
        for i in range(12):
            aid = _mk_attachment(base, storage_name=f"附件{i}.txt",
                                 content="内容" * 100)
            _promote(c, base, aid, headers)
        from backend.app.services.agent_analysis.formal_context import (
            build_formal_context,
        )
        with get_global_session_factory()() as db:
            text = build_formal_context(
                db, base["session_id"], data_dir=base["tmp"],
                max_chars=120, max_attachments=2,
            )
            assert text is not None
            # 最多 2 个附件
            assert text.count("【") <= 2
            assert len(text) <= 400