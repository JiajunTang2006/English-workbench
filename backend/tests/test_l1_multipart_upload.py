"""L1 Multipart 上传测试矩阵（长期开发方案 §8.4）。

覆盖：

- 能力声明接口（AttachmentUploadAPI v1）：限制、格式、错误码、开关；
- 正常上传：1KB / 15MB / 49MB；
- 超限：51MB 中途停止返回 413；
- 恶意/异常输入：0 字节、伪造 MIME、损坏图片、假 PDF、路径穿越；
- 故障路径：数据库提交失败、并发超限；
- 幂等：同学期同内容重复上传复用记录；
- 所有失败路径不遗留 ``.upload-*.tmp``；
- 启动清理残留临时文件；
- 上传指标记录（大小、耗时、状态，不含正文）。
"""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.services import attachment_uploads as uploads_mod

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _reset_upload_state():
    """每个用例前后重置并发计数与指标，保证断言互不干扰。"""
    uploads_mod.reset_upload_slots()
    uploads_mod.reset_upload_metrics()
    yield
    uploads_mod.reset_upload_slots()
    uploads_mod.reset_upload_metrics()


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    return TestClient(app), headers, tmp_path


def _upload(c, headers, *, filename, content, mime, title="上传", source=""):
    return c.post(
        "/api/v1/attachments/upload",
        headers=headers,
        files={"file": (filename, io.BytesIO(content), mime)},
        data={"title": title, "source": source},
    )


def _no_temp_files(tmp_path) -> bool:
    """失败路径不得遗留临时文件。"""
    return (
        list(tmp_path.glob(".upload-*.tmp")) == []
        and list(tmp_path.glob("attachments/.upload-*.tmp")) == []
    )


def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 能力声明（AttachmentUploadAPI v1）
# ---------------------------------------------------------------------------

class TestUploadCapabilities:
    def test_capabilities_expose_server_side_limits(self, client):
        c, headers, _tmp = client
        resp = c.get("/api/v1/attachments/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["api_version"] == "1.0"
        # 前端必须从这里读限制，不再硬编码 15MB
        assert body["max_attachment_bytes"] == 50 * MB
        assert body["chunk_size_bytes"] == MB
        assert body["max_concurrent_uploads"] >= 1
        assert body["dedupe_scope"] == "term_id+sha256"
        assert body["supports_cancel"] is True

    def test_capabilities_include_image_formats(self, client):
        c, headers, _tmp = client
        body = c.get("/api/v1/attachments/capabilities", headers=headers).json()
        assert ".png" in body["allowed_extensions"]
        assert ".jpg" in body["allowed_extensions"]
        assert set(body["image_extensions"]) == {".png", ".jpg", ".jpeg"}
        assert body["extension_mime_map"][".png"] == "image/png"
        # accept 属性可直接喂给 <input type=file>
        assert ".pdf" in body["accept_attribute"]

    def test_capabilities_expose_stable_error_codes(self, client):
        c, headers, _tmp = client
        codes = c.get("/api/v1/attachments/capabilities", headers=headers).json()["error_codes"]
        for expected in (
            "UPLOAD_TOO_LARGE", "UPLOAD_EMPTY_FILE", "UPLOAD_MIME_MISMATCH",
            "UPLOAD_SIGNATURE_MISMATCH", "UPLOAD_BUSY",
        ):
            assert expected in codes

    def test_capabilities_reflect_feature_flag(self, client, monkeypatch):
        c, headers, _tmp = client
        from backend.app.agent import config as agent_config

        # 确保从环境变量读取（清除任何运行时覆盖）
        monkeypatch.setattr(agent_config, "_runtime_override", None, raising=False)
        # 开关默认关闭 → 前端应回退 Base64
        assert c.get("/api/v1/attachments/capabilities", headers=headers).json()["multipart_enabled"] is False
        # 打开多部件上传开关（独立于 Agent 总开关）
        monkeypatch.setenv("AGENT_MULTIPART_UI_ENABLED", "true")
        body = c.get("/api/v1/attachments/capabilities", headers=headers).json()
        assert body["multipart_enabled"] is True

    def test_capabilities_requires_auth(self, client):
        c, _headers, _tmp = client
        assert c.get("/api/v1/attachments/capabilities").status_code in (401, 403)


# ---------------------------------------------------------------------------
# 正常上传：1KB / 15MB / 49MB
# ---------------------------------------------------------------------------

class TestNormalUploadSizes:
    @pytest.mark.parametrize("size_mb", [None, 15, 49])
    def test_upload_succeeds_across_sizes(self, client, size_mb):
        c, headers, tmp = client
        if size_mb is None:
            payload = b"a" * 1024          # 1KB
        else:
            payload = b"a" * (size_mb * MB)  # 15MB / 49MB
        resp = _upload(c, headers, filename="paper.txt", content=payload, mime="text/plain")
        assert resp.status_code == 201, resp.text
        item = resp.json()
        assert item["size_bytes"] == len(payload)
        assert item["metadata"]["security"]["upload_kind"] == "multipart"
        assert _no_temp_files(tmp)

    def test_source_marker_is_kept_for_library_filtering(self, client):
        c, headers, _tmp = client
        resp = _upload(c, headers, filename="chat.txt", content=b"chat", mime="text/plain", source="teachmate-ui")
        assert resp.status_code == 201, resp.text
        assert resp.json()["metadata"]["source"] == "teachmate-ui"

    def test_upload_image_is_accepted(self, client):
        """L1 完成定义：图片能够上传（为 L3 视觉链路准备）。"""
        c, headers, tmp = client
        resp = _upload(c, headers, filename="answer.png", content=_png_bytes(), mime="image/png")
        assert resp.status_code == 201, resp.text
        assert resp.json()["mime_type"] == "image/png"
        assert _no_temp_files(tmp)

    def test_title_defaults_to_filename(self, client):
        c, headers, _tmp = client
        resp = _upload(c, headers, filename="note.txt", content=b"hi", mime="text/plain", title="")
        assert resp.status_code == 201
        assert resp.json()["title"] == "note.txt"


# ---------------------------------------------------------------------------
# 超限：51MB 中途停止
# ---------------------------------------------------------------------------

class TestOversizeRejection:
    def test_51mb_rejected_with_code_and_no_temp_left(self, client):
        c, headers, tmp = client
        resp = _upload(c, headers, filename="huge.txt", content=b"a" * (51 * MB), mime="text/plain")
        assert resp.status_code == 413
        assert resp.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"
        assert _no_temp_files(tmp)

    def test_oversize_stops_before_reading_whole_file(self, client, monkeypatch):
        """超限必须在流式读取途中停止，不得先整文件落盘。"""
        c, headers, tmp = client
        monkeypatch.setattr("backend.app.routers.attachments.MAX_ATTACHMENT_BYTES", 2 * MB)
        resp = _upload(c, headers, filename="huge.txt", content=b"a" * (8 * MB), mime="text/plain")
        assert resp.status_code == 413
        # 没有任何正式附件文件被创建
        assert list((tmp / "attachments").glob("term_*")) == []
        assert _no_temp_files(tmp)


# ---------------------------------------------------------------------------
# 恶意与异常输入
# ---------------------------------------------------------------------------

class TestMaliciousInput:
    def test_zero_byte_rejected(self, client):
        c, headers, tmp = client
        resp = _upload(c, headers, filename="empty.txt", content=b"", mime="text/plain")
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "UPLOAD_EMPTY_FILE"
        assert _no_temp_files(tmp)

    def test_forged_mime_rejected(self, client):
        """扩展名与声明 MIME 不一致（L1 新增门禁，此前 multipart 路径缺失）。"""
        c, headers, tmp = client
        resp = _upload(c, headers, filename="report.png", content=_png_bytes(), mime="application/pdf")
        assert resp.status_code == 415
        assert resp.json()["detail"]["code"] == "UPLOAD_MIME_MISMATCH"
        assert _no_temp_files(tmp)

    def test_fake_pdf_rejected_by_magic_bytes(self, client):
        c, headers, tmp = client
        resp = _upload(c, headers, filename="fake.pdf",
                       content=b"definitely not a pdf", mime="application/pdf")
        assert resp.status_code == 415
        assert resp.json()["detail"]["code"] == "UPLOAD_SIGNATURE_MISMATCH"
        assert _no_temp_files(tmp)

    def test_corrupt_image_rejected(self, client):
        """带正确 PNG 魔数但内容被截断/破坏。"""
        c, headers, tmp = client
        corrupt = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        resp = _upload(c, headers, filename="broken.png", content=corrupt, mime="image/png")
        assert resp.status_code == 415
        assert resp.json()["detail"]["code"] in ("UPLOAD_IMAGE_INVALID", "UPLOAD_SIGNATURE_MISMATCH")
        assert _no_temp_files(tmp)

    def test_unsupported_extension_rejected(self, client):
        c, headers, tmp = client
        resp = _upload(c, headers, filename="payload.exe",
                       content=b"MZ\x90\x00", mime="application/octet-stream")
        assert resp.status_code == 415
        assert resp.json()["detail"]["code"] == "UPLOAD_UNSUPPORTED_TYPE"
        assert _no_temp_files(tmp)

    def test_path_traversal_filename_is_flattened(self, client, tmp_path):
        """路径穿越文件名必须被取 basename，不得写到附件目录之外。"""
        c, headers, tmp = client
        resp = _upload(c, headers, filename="../../../../evil.txt",
                       content=b"traversal", mime="text/plain")
        assert resp.status_code == 201, resp.text
        item = resp.json()
        # 原始文件名被取 basename，去掉了所有路径成分
        assert item["original_name"] == "evil.txt"
        assert "/" not in item["original_name"] and ".." not in item["original_name"]
        # 附件目录之外没有产生文件
        assert not (tmp.parent / "evil.txt").exists()
        stored = list((tmp / "attachments").glob("term_*"))
        assert len(stored) == 1


# ---------------------------------------------------------------------------
# 故障路径
# ---------------------------------------------------------------------------

class TestFailurePaths:
    def test_db_commit_failure_cleans_files(self, client, monkeypatch):
        """数据库提交失败：不留正式文件、不留临时文件。"""
        c, headers, tmp = client
        from sqlalchemy.orm import Session

        def boom(self, *args, **kwargs):
            raise RuntimeError("提交失败（测试注入）")

        monkeypatch.setattr(Session, "commit", boom)
        with pytest.raises(RuntimeError):
            _upload(c, headers, filename="tx.txt", content=b"payload", mime="text/plain")
        assert list((tmp / "attachments").glob("term_*")) == []
        assert _no_temp_files(tmp)

    def test_concurrency_limit_returns_429(self, client):
        """并发上传超过上限返回 429 UPLOAD_BUSY。"""
        c, headers, tmp = client
        # 预占满所有名额，模拟已有大文件在写盘
        limit = uploads_mod.DEFAULT_MAX_CONCURRENT_UPLOADS
        for _ in range(limit):
            uploads_mod._active_uploads += 1
        try:
            resp = _upload(c, headers, filename="queued.txt", content=b"x", mime="text/plain")
        finally:
            uploads_mod.reset_upload_slots()
        assert resp.status_code == 429
        body = resp.json()["detail"]
        assert body["code"] == "UPLOAD_BUSY"
        assert body["max_concurrent_uploads"] == limit
        assert _no_temp_files(tmp)

    def test_slot_is_released_after_success(self, client):
        c, headers, _tmp = client
        assert uploads_mod.active_upload_count() == 0
        assert _upload(c, headers, filename="a.txt", content=b"a", mime="text/plain").status_code == 201
        assert uploads_mod.active_upload_count() == 0

    def test_slot_is_released_after_failure(self, client):
        c, headers, _tmp = client
        assert _upload(c, headers, filename="a.pdf", content=b"nope", mime="application/pdf").status_code == 415
        assert uploads_mod.active_upload_count() == 0


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_same_content_returns_same_record(self, client):
        c, headers, tmp = client
        first = _upload(c, headers, filename="one.txt", content=b"same-bytes", mime="text/plain")
        second = _upload(c, headers, filename="two.txt", content=b"same-bytes", mime="text/plain")
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert len(list((tmp / "attachments").glob("term_*"))) == 1

    def test_dedupe_is_recorded_in_metrics(self, client):
        c, headers, _tmp = client
        _upload(c, headers, filename="one.txt", content=b"dupe", mime="text/plain")
        _upload(c, headers, filename="two.txt", content=b"dupe", mime="text/plain")
        snapshot = uploads_mod.upload_metrics_snapshot()
        assert snapshot["succeeded"] == 1
        assert snapshot["deduped"] == 1


# ---------------------------------------------------------------------------
# 临时文件启动清理
# ---------------------------------------------------------------------------

class TestStartupTempCleanup:
    def test_cleanup_removes_stale_temp_files(self, tmp_path):
        directory = tmp_path / "attachments"
        directory.mkdir()
        stale = directory / ".upload-deadbeef.tmp"
        stale.write_bytes(b"x" * 128)
        keep = directory / "term_1_realfile.txt"
        keep.write_bytes(b"keep me")

        stats = uploads_mod.cleanup_stale_upload_temp_files(directory)
        assert stats["removed"] == 1
        assert stats["bytes"] == 128
        assert not stale.exists()
        assert keep.exists()  # 正式附件不受影响

    def test_cleanup_respects_age_threshold(self, tmp_path):
        directory = tmp_path / "attachments"
        directory.mkdir()
        fresh = directory / ".upload-inflight.tmp"
        fresh.write_bytes(b"in progress")
        # 阈值 1 小时：刚创建的文件视为进行中，不清理
        stats = uploads_mod.cleanup_stale_upload_temp_files(directory, max_age_seconds=3600)
        assert stats["removed"] == 0
        assert fresh.exists()

    def test_cleanup_handles_missing_directory(self, tmp_path):
        stats = uploads_mod.cleanup_stale_upload_temp_files(tmp_path / "nope")
        assert stats == {"removed": 0, "failed": 0, "bytes": 0}

    def test_app_startup_clears_leftover_temp_files(self, tmp_path):
        """应用启动（lifespan）时清理上次强杀残留的临时文件。"""
        settings = Settings(data_dir=tmp_path)
        leftover = settings.attachments_dir / ".upload-crashed.tmp"
        leftover.parent.mkdir(parents=True, exist_ok=True)
        leftover.write_bytes(b"leftover")
        app = create_app(settings)
        with TestClient(app):  # 进入 lifespan
            assert not leftover.exists()


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------

class TestUploadMetrics:
    def test_success_metrics_recorded_without_content(self, client):
        c, headers, _tmp = client
        payload = b"m" * 4096
        _upload(c, headers, filename="metric.txt", content=payload, mime="text/plain")
        snapshot = uploads_mod.upload_metrics_snapshot()
        assert snapshot["total"] == 1
        assert snapshot["succeeded"] == 1
        assert snapshot["total_bytes"] == len(payload)
        # 指标只含标量，不含文件名/正文/哈希
        assert set(snapshot) == {
            "total", "succeeded", "failed", "deduped",
            "total_bytes", "average_duration_ms", "by_code",
        }

    def test_failure_metrics_record_error_code(self, client):
        c, headers, _tmp = client
        _upload(c, headers, filename="bad.pdf", content=b"nope", mime="application/pdf")
        snapshot = uploads_mod.upload_metrics_snapshot()
        assert snapshot["failed"] == 1
        assert snapshot["by_code"]["UPLOAD_SIGNATURE_MISMATCH"] == 1

    def test_metrics_exposed_through_capabilities(self, client):
        c, headers, _tmp = client
        _upload(c, headers, filename="x.txt", content=b"x", mime="text/plain")
        body = c.get("/api/v1/attachments/capabilities", headers=headers).json()
        assert body["metrics"]["succeeded"] == 1


# ---------------------------------------------------------------------------
# 兼容与不回归
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_legacy_base64_endpoint_still_works(self, client):
        """旧 Base64 入口保留一个版本周期（方案 §8.3）。"""
        import base64

        c, headers, _tmp = client
        resp = c.post("/api/v1/attachments", headers=headers, json={
            "title": "legacy", "original_name": "legacy.txt", "mime_type": "text/plain",
            "content_base64": base64.b64encode(b"legacy path").decode(),
        })
        assert resp.status_code == 201

    def test_multipart_and_base64_share_dedupe(self, client):
        import base64

        c, headers, _tmp = client
        content = b"shared-content"
        m = _upload(c, headers, filename="a.txt", content=content, mime="text/plain")
        b = c.post("/api/v1/attachments", headers=headers, json={
            "title": "b", "original_name": "b.txt", "mime_type": "text/plain",
            "content_base64": base64.b64encode(content).decode(),
        })
        assert m.json()["id"] == b.json()["id"]

    def test_uploaded_file_is_listable_and_downloadable(self, client):
        c, headers, _tmp = client
        content = b"round trip"
        item = _upload(c, headers, filename="rt.txt", content=content, mime="text/plain").json()
        listing = c.get("/api/v1/attachments", headers=headers).json()
        assert any(row["id"] == item["id"] for row in listing)
        down = c.get(f"/api/v1/attachments/{item['id']}/download", headers=headers)
        assert down.content == content
