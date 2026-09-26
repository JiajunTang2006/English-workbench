"""U4-02 附件上传与解析管线测试

测试要求：
- 复用 document_parser.py、exam_ingestion.py、ocr.py、agent_message_attachments
- 工作流：上传 → 文件校验 → 后台解析 → 教师校对 → 晋升为正式资料
- 必须支持 PDF、图片、Excel、Word DOCX；旧版 .doc 需转换
- 解析结果在教师确认前不得进入正式考试事实表
- 重复上传不会创建两份正式资料（幂等性）
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.database import Base
from backend.app.models.agent_entities import (
    AgentMessageAttachment,
    AgentSession,
    AgentMessage,
    BackgroundJob,
)
from backend.app.models.entities import Attachment, Term
from backend.app.services.document_parser import DocumentParser
from backend.app.services.job_worker import (
    JobWorker,
    submit_job,
    set_global_worker,
)
from backend.app.services.job_handlers.base import register_all_handlers


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_factory():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_u402_")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()
    os.unlink(db_path)


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "attachments").mkdir()
    return d


@pytest.fixture
def term(session_factory):
    with session_factory() as session:
        t = Term(
            code="2025-fall",
            name="2025秋季",
            starts_on=date(2025, 9, 1),
            ends_on=date(2026, 1, 31),
            status="active",
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


@pytest.fixture
def worker(session_factory):
    w = JobWorker(
        session_factory,
        poll_interval=0.05,
        heartbeat_interval=0.5,
        shutdown_grace=3.0,
    )
    register_all_handlers(w)
    w.start()
    yield w
    w.stop(timeout=2.0)


def _create_attachment(
    session_factory,
    term_id: int,
    *,
    content: bytes,
    original_name: str = "test.txt",
    mime_type: str = "text/plain",
    data_dir: Path = None,
) -> int:
    digest = hashlib.sha256(content).hexdigest()
    suffix = Path(original_name).suffix.lower()
    storage_name = f"term_{term_id}_{hashlib.sha256(original_name.encode()).hexdigest()[:16]}{suffix}"
    if data_dir is not None:
        (data_dir / "attachments" / storage_name).write_bytes(content)
    with session_factory() as session:
        att = Attachment(
            term_id=term_id,
            title=original_name,
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=digest,
            storage_name=storage_name,
            metadata_json={},
        )
        session.add(att)
        session.commit()
        session.refresh(att)
        return att.id


# ---------------------------------------------------------------------------
# DocumentParser 测试
# ---------------------------------------------------------------------------

class TestDocumentParser:
    def test_parse_text_file(self, data_dir):
        parser = DocumentParser()
        f = data_dir / "test.txt"
        f.write_text("Hello World\n测试文本", encoding="utf-8")
        result = parser.parse(f)
        assert result["format"] == "text"
        assert "Hello World" in result["content"]
        assert result["error"] is None
        assert result["needs_ocr"] is False

    def test_parse_csv_file(self, data_dir):
        parser = DocumentParser()
        f = data_dir / "scores.csv"
        f.write_text("name,score\nAlice,95\nBob,87\n", encoding="utf-8")
        result = parser.parse(f)
        assert result["format"] == "csv"
        assert "Alice" in result["content"]
        assert result["metadata"]["row_count"] == 3
        assert result["metadata"]["col_count"] == 2

    def test_parse_markdown_file(self, data_dir):
        parser = DocumentParser()
        f = data_dir / "notes.md"
        f.write_text("# Title\n\nSome text.", encoding="utf-8")
        result = parser.parse(f)
        assert result["format"] == "markdown"
        assert "# Title" in result["content"]

    def test_parse_image_file(self, data_dir):
        from PIL import Image
        parser = DocumentParser()
        f = data_dir / "exam.png"
        Image.new("RGB", (800, 600), color="white").save(str(f))
        result = parser.parse(f)
        assert result["format"] == "image"
        assert result["needs_ocr"] is True
        assert result["metadata"]["width"] == 800
        assert result["metadata"]["height"] == 600

    def test_parse_excel_file(self, data_dir):
        from openpyxl import Workbook
        parser = DocumentParser()
        f = data_dir / "scores.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["name", "score"])
        ws.append(["Alice", 95])
        ws.append(["Bob", 87])
        wb.save(str(f))
        wb.close()
        result = parser.parse(f)
        assert result["format"] == "excel"
        assert "Alice" in result["content"]
        assert result["metadata"]["sheet_count"] == 1
        assert result["metadata"]["sheets"][0]["row_count"] == 3

    def test_parse_unsupported_format(self, data_dir):
        parser = DocumentParser()
        f = data_dir / "test.xyz"
        f.write_bytes(b"binary data")
        result = parser.parse(f)
        assert result["format"] == "unknown"
        assert result["error"] is not None
        assert "不支持" in result["error"]

    def test_parse_nonexistent_file(self, data_dir):
        parser = DocumentParser()
        result = parser.parse(data_dir / "nonexistent.txt")
        assert result["error"] is not None
        assert "不存在" in result["error"]

    def test_parse_text_with_gbk_encoding(self, data_dir):
        parser = DocumentParser()
        f = data_dir / "gbk.txt"
        f.write_bytes("中文测试".encode("gbk"))
        result = parser.parse(f)
        assert result["format"] == "text"
        assert "中文测试" in result["content"]

    def test_parse_docx_file(self, data_dir):
        """DOCX 段落和表格正文可被解析，供模型上下文读取。"""
        import zipfile

        parser = DocumentParser()
        f = data_dir / "doc.docx"
        document_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>考试说明</w:t></w:r></w:p>
    <w:p><w:r><w:t>请重点复习一般过去时。</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>姓名</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>分数</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>张三</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>95</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:sectPr/>
  </w:body>
</w:document>'''
        with zipfile.ZipFile(f, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
            archive.writestr("word/document.xml", document_xml)

        result = parser.parse(f)
        assert result["format"] == "word"
        assert result["error"] is None
        assert "考试说明" in result["content"]
        assert "一般过去时" in result["content"]
        assert "张三\t95" in result["content"]
        assert result["metadata"]["paragraph_count"] == 2
        assert result["metadata"]["table_count"] == 1

    def test_corrupt_docx_returns_error(self, data_dir):
        parser = DocumentParser()
        f = data_dir / "broken.docx"
        f.write_bytes(b"not a zip")
        result = parser.parse(f)
        assert result["format"] == "word"
        assert result["error"] is not None
        assert result["content"] == ""


# ---------------------------------------------------------------------------
# 附件解析后台任务测试
# ---------------------------------------------------------------------------

class TestAttachmentParseJob:
    def test_parse_text_attachment(self, session_factory, data_dir, term, worker):
        """文本附件通过后台解析，结果写入 metadata。"""
        att_id = _create_attachment(
            session_factory, term,
            content=b"Exam Paper 2025\nQuestion 1: Hello",
            original_name="exam.txt",
            data_dir=data_dir,
        )
        job_id = submit_job(
            session_factory,
            "attachment_parse",
            {
                "attachment_id": att_id,
                "purpose": "exam_paper",
                "settings_data_dir": str(data_dir),
            },
            idempotency_key=f"parse_{att_id}",
        )
        assert job_id > 0

        # 等待任务完成
        for _ in range(50):
            with session_factory() as session:
                job = session.get(BackgroundJob, job_id)
                if job and job.status == "completed":
                    break
            time.sleep(0.1)
        else:
            pytest.fail("任务未在预期时间内完成")

        with session_factory() as session:
            att = session.get(Attachment, att_id)
            parsed = att.metadata_json.get("parsed", {})
            assert parsed["format"] == "text"
            assert "Exam Paper 2025" in parsed["content"]
            assert parsed["status"] == "pending_review"
            assert parsed["purpose"] == "exam_paper"
            assert parsed["error"] is None

    def test_parse_image_attachment_needs_ocr(self, session_factory, data_dir, term, worker):
        """图片附件解析后标记 needs_ocr。"""
        from PIL import Image
        img_path = data_dir / "exam_img.png"
        Image.new("RGB", (400, 300), color="white").save(str(img_path))
        att_id = _create_attachment(
            session_factory, term,
            content=img_path.read_bytes(),
            original_name="exam_img.png",
            mime_type="image/png",
            data_dir=data_dir,
        )
        job_id = submit_job(
            session_factory, "attachment_parse",
            {"attachment_id": att_id, "purpose": "student_exam_image",
             "settings_data_dir": str(data_dir)},
        )
        job_status = None
        for _ in range(50):
            with session_factory() as session:
                job = session.get(BackgroundJob, job_id)
                if job and job.status in ("waiting_ocr", "completed", "failed"):
                    job_status = job.status
                    break
            time.sleep(0.1)

        # S0-05：图片等待 OCR 是明确的待处理状态，不得伪装成完全成功
        assert job_status == "waiting_ocr", f"图片附件任务状态应为 waiting_ocr，实际 {job_status}"

        with session_factory() as session:
            att = session.get(Attachment, att_id)
            parsed = att.metadata_json.get("parsed", {})
            assert parsed["needs_ocr"] is True
            assert parsed["status"] == "pending_ocr"
            assert parsed["format"] == "image"