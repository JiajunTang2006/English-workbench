"""U4-03 附件安全与资源限制测试。"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.services import attachment_security
from backend.app.services.attachment_security import (
    safe_storage_path,
    validate_stored_file,
    validate_upload,
)
from backend.app.services.document_parser import DocumentParser


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_rejects_extension_mime_mismatch():
    with pytest.raises(ValueError, match="MIME"):
        validate_upload("paper.pdf", "text/plain", b"%PDF-1.7")


def test_accepts_valid_docx_and_rejects_fake_docx():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"/>")
    raw = buf.getvalue()
    safe_name, mime, _ = validate_upload(
        "lesson.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        raw,
    )
    assert safe_name == "lesson.docx"
    assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    with pytest.raises(ValueError, match="有效 Word DOCX"):
        validate_upload(
            "lesson.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\\x03\\x04fake",
        )


def test_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        safe_storage_path(tmp_path, "../outside.txt")
    with pytest.raises(ValueError):
        safe_storage_path(tmp_path, str(tmp_path / "outside.txt"))


def test_rejects_corrupted_stored_file(tmp_path):
    path = tmp_path / "paper.txt"
    path.write_bytes(b"hello")
    with pytest.raises(ValueError, match="SHA-256"):
        validate_stored_file(path, expected_size=5, expected_sha256="0" * 64)


def test_rejects_oversized_image(monkeypatch):
    raw = b"\x89PNG\r\n\x1a\n" + b"placeholder"

    class FakeImage:
        width = 10_000
        height = 10_000
        size = (10_000, 10_000)
        format = "PNG"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def verify(self):
            return None

    monkeypatch.setattr(attachment_security.Image, "open", lambda *_args, **_kwargs: FakeImage())
    with pytest.raises(ValueError, match="像素数"):
        validate_upload("huge.png", "image/png", raw)


def test_document_parser_timeout(monkeypatch, tmp_path):
    path = tmp_path / "slow.txt"
    path.write_text("content", encoding="utf-8")

    def slow_handler(_path):
        time.sleep(0.2)
        return {"format": "text", "content": "late"}

    parser = DocumentParser()
    monkeypatch.setattr(parser, "_parse_text", slow_handler)
    result = parser.parse(path, timeout_seconds=0.01)
    assert result["error"] == "文档解析超时"
    assert result["content"] == ""


def test_upload_rejects_fake_pdf_and_duplicate_is_idempotent(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = _headers()
    term = client.get("/api/v1/terms/current", headers=headers).json()

    fake = client.post(
        f"/api/v1/attachments?term_id={term['id']}",
        headers=headers,
        json={
            "title": "fake",
            "original_name": "paper.pdf",
            "mime_type": "application/pdf",
            "content_base64": base64.b64encode(b"not pdf").decode(),
        },
    )
    assert fake.status_code == 415

    payload = {
        "title": "notes",
        "original_name": "notes.txt",
        "mime_type": "text/plain",
        "content_base64": base64.b64encode(b"same content").decode(),
    }
    first = client.post(f"/api/v1/attachments?term_id={term['id']}", headers=headers, json=payload)
    second = client.post(f"/api/v1/attachments?term_id={term['id']}", headers=headers, json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get(f"/api/v1/attachments?term_id={term['id']}", headers=headers).json()) == 1
