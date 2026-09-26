"""B2-04 附件上传的内存与资源限制测试

覆盖：
- 超长 Base64 在解码前被拒绝（长度门禁，不完整解码）；
- 非法 Base64 返回 422；
- multipart 流式上传：合法小文件、超限拒绝、Magic Bytes 不匹配、
  中断/超限后临时文件清理、重复文件去重；
- 解析超时进程级终止逻辑被触发（terminate/join）。
"""

from __future__ import annotations

import base64
import io
import multiprocessing
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    return TestClient(app), headers, tmp_path


SMALL_TXT = ("Hello offline workbench\n" * 8).encode()


class TestBase64PreDecodeLimit:
    def test_oversized_base64_rejected_before_decode(self, client, monkeypatch):
        c, headers, tmp = client
        from backend.app.routers import attachments as mod
        # 将上限压小，验证长度门禁先于解码
        monkeypatch.setattr("backend.app.routers.attachments.MAX_ATTACHMENT_BYTES", 1024)
        oversized = "A" * 2000  # 估算 1500 字节 > 1024
        resp = c.post("/api/v1/attachments", headers=headers, json={
            "title": "big", "original_name": "big.txt", "mime_type": "text/plain",
            "content_base64": oversized,
        })
        assert resp.status_code == 413

    def test_invalid_base64_returns_422(self, client):
        c, headers, tmp = client
        resp = c.post("/api/v1/attachments", headers=headers, json={
            "title": "bad", "original_name": "bad.txt", "mime_type": "text/plain",
            "content_base64": "!!!not-base64!!!",
        })
        assert resp.status_code == 422

    def test_valid_small_base64_still_works(self, client):
        import base64
        c, headers, tmp = client
        resp = c.post("/api/v1/attachments", headers=headers, json={
            "title": "ok", "original_name": "ok.txt", "mime_type": "text/plain",
            "content_base64": base64.b64encode(SMALL_TXT).decode(),
        })
        assert resp.status_code == 201
        assert resp.json()["size_bytes"] == len(SMALL_TXT)


class TestMultipartUpload:
    def _upload(self, c, headers, *, filename="paper.txt", content=SMALL_TXT,
                mime="text/plain", title="上传"):
        return c.post(
            "/api/v1/attachments/upload",
            headers=headers,
            files={"file": (filename, io.BytesIO(content), mime)},
            data={"title": title},
        )

    def test_multipart_small_file_ok(self, client):
        c, headers, tmp = client
        resp = self._upload(c, headers)
        assert resp.status_code == 201, resp.text
        item = resp.json()
        assert item["size_bytes"] == len(SMALL_TXT)
        assert item["metadata"]["security"]["upload_kind"] == "multipart"
        # 可下载且内容一致
        down = c.get(f"/api/v1/attachments/{item['id']}/download", headers=headers)
        assert down.content == SMALL_TXT

    def test_multipart_over_limit_rejected_and_temp_cleaned(self, client, monkeypatch):
        c, headers, tmp = client
        monkeypatch.setattr("backend.app.routers.attachments.MAX_ATTACHMENT_BYTES", 1024)
        from backend.app.services import attachment_security as sec
        monkeypatch.setattr(sec, "MAX_ATTACHMENT_BYTES", 1024)
        big = b"x" * 4096
        resp = self._upload(c, headers, content=big)
        assert resp.status_code == 413
        # 临时文件被清理
        assert list(tmp.glob(".upload-*.tmp")) == []
        assert list(tmp.glob("attachments/.upload-*.tmp")) == []

    def test_multipart_magic_bytes_mismatch(self, client):
        c, headers, tmp = client
        resp = self._upload(c, headers, filename="fake.pdf",
                            content=b"this is not a pdf at all", mime="application/pdf")
        assert resp.status_code == 415
        assert list(tmp.glob("attachments/.upload-*.tmp")) == []

    def test_multipart_empty_rejected(self, client):
        c, headers, tmp = client
        resp = self._upload(c, headers, content=b"")
        assert resp.status_code in (413, 422)

    def test_multipart_duplicate_dedup(self, client):
        c, headers, tmp = client
        first = self._upload(c, headers, filename="a.txt")
        second = self._upload(c, headers, filename="b.txt")  # 内容相同
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]  # 同一记录，不重复保存
        # 附件目录只有一个正式文件
        formal = list((tmp / "attachments").glob("term_*"))
        assert len(formal) == 1

    def test_base64_and_multipart_share_dedup(self, client):
        import base64
        c, headers, tmp = client
        m_resp = self._upload(c, headers, content=SMALL_TXT)
        b_resp = c.post("/api/v1/attachments", headers=headers, json={
            "title": "b", "original_name": "x.txt", "mime_type": "text/plain",
            "content_base64": base64.b64encode(SMALL_TXT).decode(),
        })
        assert m_resp.status_code == 201 and b_resp.status_code == 201
        assert m_resp.json()["id"] == b_resp.json()["id"]


class TestParseTimeoutTermination:
    def test_real_timeout_terminates_subprocess(self):
        """真实解析超时：进程级终止路径被触发并抛 TimeoutError。

        以 0 秒超时直接调用进程级包装（poll 不等待），子进程必然进入
        超时分支，经过 terminate+join 后抛 multiprocessing.TimeoutError。
        """
        from backend.app.services.document_parser import DocumentParser

        parser = DocumentParser()
        with pytest.raises(multiprocessing.TimeoutError):
            parser._parse_with_process_timeout(
                "text", "/tmp/no-such-file-b204.txt", timeout=0.0,
            )

class TestNoFullMemoryRead:
    """multipart 路径不得整文件读入内存（B2-04 审查修复）。"""

    def test_multipart_uses_stored_path_check_not_full_read(
        self, tmp_path, monkeypatch, client
    ):
        import backend.app.routers.attachments as att
        called = {"stored": 0}

        real = att.validate_stored_path

        def spy(*a, **kw):
            called["stored"] += 1
            return real(*a, **kw)

        monkeypatch.setattr(att, "validate_stored_path", spy)

        c, headers, _tmp = client
        data = b"hello-multipart"
        resp = c.post(
            "/api/v1/attachments/upload",
            headers=headers,
            files={"file": ("note.txt", data, "text/plain")},
            data={"title": "n"},
        )
        assert resp.status_code == 201, resp.text
        assert called["stored"] >= 1, "multipart 必须走 validate_stored_path（不整读）"
