from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.entities import Term
from backend.app.models.agent_entities import AgentSession


def test_session_preferences_are_scoped_and_persisted(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with app.state.session_factory() as db:
        term = Term(code="pref-term", name="偏好学期", status="active")
        db.add(term)
        db.commit()
        db.refresh(term)
        item = AgentSession(term_id=term.id, title="会话", status="active")
        db.add(item)
        db.commit()
        db.refresh(item)
        term_id, session_id = term.id, item.id

    payload = {"term_id": term_id, "pinned_session_ids": [session_id, 999999], "folders": [{"id": "f1", "name": "期中", "sessionIds": [session_id, 999999], "collapsed": True}]}
    saved = client.put("/api/v1/agent/session-preferences", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["pinned_session_ids"] == [session_id]
    assert saved.json()["folders"][0]["sessionIds"] == [session_id]
    loaded = client.get(f"/api/v1/agent/session-preferences?term_id={term_id}", headers=headers)
    assert loaded.status_code == 200
    assert loaded.json()["folders"][0]["name"] == "期中"
