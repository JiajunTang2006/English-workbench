from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import AgentMessage, AgentMessageAttachment, AgentSession


def test_attachment_metadata_and_file_lifecycle(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    content = b"offline paper attachment"

    created = client.post(
        "/api/v1/attachments",
        headers=headers,
        json={
            "title": "原卷第3页",
            "original_name": "paper.txt",
            "mime_type": "text/plain",
            "content_base64": base64.b64encode(content).decode(),
            "metadata": {"page": 3},
        },
    )
    assert created.status_code == 201, created.text
    attachment = created.json()
    assert attachment["metadata"] == {"page": 3}
    assert attachment["size_bytes"] == len(content)

    listed = client.get("/api/v1/attachments", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == attachment["id"]

    downloaded = client.get(
        f"/api/v1/attachments/{attachment['id']}/download", headers=headers,
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content

    deleted = client.delete(
        f"/api/v1/attachments/{attachment['id']}", headers=headers,
    )
    assert deleted.status_code == 204
    assert client.get(
        f"/api/v1/attachments/{attachment['id']}/download", headers=headers,
    ).status_code == 404


def test_delete_with_workspace_updates_revision_atomically(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    term = client.get("/api/v1/terms/current", headers=headers).json()
    created = client.post(
        f"/api/v1/attachments?term_id={term['id']}",
        headers=headers,
        json={
            "title": "原卷",
            "original_name": "paper.txt",
            "mime_type": "text/plain",
            "content_base64": base64.b64encode(b"paper").decode(),
            "metadata": {},
        },
    ).json()
    saved = client.put(
        f"/api/v1/terms/{term['id']}/workspace-state",
        headers=headers,
        json={
            "expected_revision": 0,
            "state": {"paperDocuments": [{"id": "doc-1", "attachmentId": created["id"]}], "errors": [{"id": "error-1"}]},
        },
    ).json()
    with client.app.state.session_factory() as session:
        chat = AgentSession(term_id=term["id"], title="删除测试")
        chat.messages.append(AgentMessage(role="user", content_text="附件引用"))
        session.add(chat)
        session.flush()
        chat.messages[0].attachments.append(AgentMessageAttachment(attachment_id=created["id"]))
        session.commit()

    conflict = client.post(
        f"/api/v1/attachments/{created['id']}/delete-with-workspace",
        headers=headers,
        json={"term_id": term["id"], "expected_revision": saved["revision"] - 1, "document_id": "doc-1", "error_ids": ["error-1"]},
    )
    assert conflict.status_code == 409
    assert client.get(f"/api/v1/attachments/{created['id']}/download", headers=headers).status_code == 200

    deleted = client.post(
        f"/api/v1/attachments/{created['id']}/delete-with-workspace",
        headers=headers,
        json={"term_id": term["id"], "expected_revision": saved["revision"], "document_id": "doc-1", "error_ids": ["error-1"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["revision"] == saved["revision"] + 1
    assert deleted.json()["state"]["paperDocuments"] == []
    assert deleted.json()["state"]["archivedDocuments"] == []
    assert deleted.json()["state"]["errors"] == []
    with client.app.state.session_factory() as session:
        assert session.query(AgentMessageAttachment).filter(AgentMessageAttachment.attachment_id == created["id"]).count() == 0


def test_delete_with_workspace_is_idempotent_for_stale_attachment(tmp_path):
    """前端残留旧文件卡片时，删除请求仍应清理工作台状态而不是报错。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    term = client.get("/api/v1/terms/current", headers=headers).json()
    saved = client.put(
        f"/api/v1/terms/{term['id']}/workspace-state",
        headers=headers,
        json={
            "expected_revision": 0,
            "state": {
                "paperDocuments": [{"id": "attachment-999", "attachmentId": 999}],
                "errors": [{"id": "error-1", "documentId": "attachment-999"}],
            },
        },
    ).json()
    deleted = client.post(
        "/api/v1/attachments/999/delete-with-workspace",
        headers=headers,
        json={
            "term_id": term["id"],
            "expected_revision": saved["revision"],
            "document_id": "attachment-999",
            "error_ids": ["error-1"],
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["state"]["paperDocuments"] == []
    assert deleted.json()["state"]["errors"] == []
