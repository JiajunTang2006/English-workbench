"""L4 文档导出测试（PDF/DOCX 本地生成 + 开关 fail-closed + 产物登记）。

覆盖：
- document_export_enabled=False → /export 返回 503 document_export_disabled；
- enabled + pdf → 200，本地生成产物（weasyprint 可用则为 PDF，否则 HTML 兜底），落盘登记；
- enabled + docx → 200，生成合法 OOXML（zip 结构 + 关键成员齐全）；
- provider.type=remote 且未开 remote_document_provider_enabled → 409；
- 同一 spec 第二次请求幂等复用既有产物（reused=True）；
- /download/{artifact_id} 返回文件并携带 SHA-256 头；
- 防穿越：storage_name 含路径分隔符被安全层拒绝（400）。
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app.agent.config import get_agent_config
from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.entities import Term
from backend.app.models.agent_entities import AgentMessage, AnalysisEvidence
from sqlalchemy import select


def _seed(session, *, with_message: bool = True):
    active_id = session.scalar(
        select(Term.id).where(Term.status == "active").order_by(Term.id).limit(1)
    )
    if active_id is None:
        term = Term(code="2026FA", name="2026 秋", status="active")
        session.add(term)
        session.flush()
        active_id = term.id
    session_id = None
    if with_message:
        from backend.app.models.agent_entities import AgentSession, AnalysisRun

        sess = AgentSession(title="测试会话", term_id=active_id)
        session.add(sess)
        session.flush()
        run = AnalysisRun(
            session_id=sess.id, capability="exam_analysis", term_id=active_id,
            status="completed", rules_version="v1.0.0",
        )
        session.add(run)
        session.flush()
        msg = AgentMessage(
            session_id=sess.id, analysis_run_id=run.id, role="assistant",
            structured_answer_json={
                "answer_type": "exam_analysis",
                "summary": "班级整体达标。",
                "findings": [
                    {"title": "听力薄弱", "description": "听力得分率偏低",
                     "evidence_ids": ["E1"]},
                ],
                "recommendations": [
                    {"action": "加强听力训练", "priority": 1, "supports": ["E1"]},
                ],
                "limitations": ["样本仅一次考试"],
                "schema_version": "1.0.0",
            },
            evidence_ids_json=["E1"],
        )
        session.add(msg)
        session.flush()
        session_id = sess.id
        ev = AnalysisEvidence(
            evidence_id="E1", run_id=run.id, evidence_type="db_metric",
            display_summary="听力得分率 0.62", source_file="exam.xlsx",
            source_page=1, contains_personal_data=False,
        )
        session.add(ev)
        session.commit()
    return {"term_id": active_id, "session_id": session_id}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DOCUMENT_EXPORT_ENABLED", "true")
    monkeypatch.setenv("AGENT_REMOTE_DOCUMENT_PROVIDER_ENABLED", "false")
    import backend.app.agent.config as ac
    monkeypatch.setattr(ac, "_runtime_override", None, raising=False)
    app = create_app(Settings(data_dir=tmp_path))
    ids = _seed(app.state.session_factory())
    return TestClient(app), {"Authorization": f"Bearer {TOKEN}"}, ids


@pytest.fixture
def disabled_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DOCUMENT_EXPORT_ENABLED", "false")
    import backend.app.agent.config as ac
    monkeypatch.setattr(ac, "_runtime_override", None, raising=False)
    app = create_app(Settings(data_dir=tmp_path))
    ids = _seed(app.state.session_factory())
    return TestClient(app), {"Authorization": f"Bearer {TOKEN}"}, ids


def _spec(fmt: str, session_id: int, *, remote: bool = False):
    return {
        "format": fmt,
        "template": "report",
        "title": "初三(2)班 期中英语分析",
        "locale": "zh-CN",
        "source": {"session_ids": [session_id], "attachment_ids": []},
        "provider": {"type": "remote" if remote else "local", "remote_enabled": remote},
        "options": {},
    }


def _spec_inline(fmt: str, answer: dict):
    """模拟前端 L4-D：直接把屏幕结构化答案作为 inline_content 传入。"""
    return {
        "format": fmt,
        "template": "report",
        "title": answer.get("title") or answer.get("summary") or "TeachMate 分析报告",
        "locale": "zh-CN",
        "source": {"session_ids": [], "attachment_ids": []},
        "provider": {"type": "local", "remote_enabled": False},
        "options": {},
        "inline_content": answer,
    }


SAMPLE_INLINE_ANSWER = {
    "answer_type": "exam_analysis",
    "title": "初三(2)班 期中英语分析",
    "summary": "班级整体达标，听力板块偏弱。",
    "findings": [
        {"title": "听力薄弱", "description": "听力得分率 0.62，低于班级均值。",
         "evidence_ids": ["E1"]},
    ],
    "recommendations": [
        {"action": "增加听力训练频次", "priority": 1, "supports": ["E1"]},
    ],
    "limitations": ["样本仅一次考试"],
    "evidence": [
        {"evidence_id": "E1", "evidence_type": "db_metric",
         "display_summary": "听力得分率 0.62", "source_file": "exam.xlsx"},
    ],
    "schema_version": "1.0.0",
}



def test_disabled_returns_503(disabled_client):
    c, headers, ids = disabled_client
    r = c.post("/api/v1/documents/export",
               json=_spec("pdf", ids["session_id"]), headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "document_export_disabled"


def test_pdf_export_creates_artifact(client):
    c, headers, ids = client
    r = c.post("/api/v1/documents/export",
               json=_spec("pdf", ids["session_id"]), headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["artifact"] is not None
    art = body["artifact"]
    # PDF 或 HTML 兜底，二者其一
    assert body["produced_format"] in ("pdf", "html")
    assert art["size_bytes"] > 0
    assert len(art["sha256"]) == 64
    assert art["contains_personal_data"] is True
    # 文件确实落盘
    p = client[0].app.state.settings.exports_dir / art["storage_name"]
    assert p.exists()
    if body["produced_format"] == "pdf":
        assert art["mime_type"] == "application/pdf"
    else:
        # HTML 兜底：内容可离线打开
        assert art["mime_type"] == "text/html"


def test_docx_export_valid_ooxml(client):
    c, headers, ids = client
    r = c.post("/api/v1/documents/export",
               json=_spec("docx", ids["session_id"]), headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["produced_format"] == "docx"
    art = body["artifact"]
    assert art["mime_type"].endswith("wordprocessingml.document")
    p = client[0].app.state.settings.exports_dir / art["storage_name"]
    data = p.read_bytes()
    # 合法 OOXML 包：zip + 关键成员 + 无损坏成员
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    required = {
        "[Content_Types].xml", "_rels/.rels", "word/document.xml",
        "word/styles.xml", "word/numbering.xml",
        "word/header1.xml", "word/footer1.xml", "docProps/core.xml",
    }
    assert required.issubset(set(z.namelist()))
    # 正文含中文标题
    doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "初三" in doc_xml or "测试" in doc_xml or "TeachMate" in doc_xml


def test_remote_requires_enabled_flag(client):
    c, headers, ids = client
    # client 已关 remote_document_provider_enabled
    r = c.post("/api/v1/documents/export",
               json=_spec("pdf", ids["session_id"], remote=True), headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "remote_provider_not_enabled"


def test_idempotent_reuse(client):
    c, headers, ids = client
    spec = _spec("docx", ids["session_id"])
    r1 = c.post("/api/v1/documents/export", json=spec, headers=headers)
    assert r1.status_code == 200
    job1 = r1.json()["job_id"]
    r2 = c.post("/api/v1/documents/export", json=spec, headers=headers)
    assert r2.status_code == 200
    body2 = r2.json()
    # 第二次应复用同一产物且未重复计费/生成
    assert body2["reused"] is True
    assert body2["artifact"]["id"] == r1.json()["artifact"]["id"]
    # 产物文件只生成一次（落盘名相同，sha256 一致）
    assert body2["artifact"]["sha256"] == r1.json()["artifact"]["sha256"]


def test_download_and_traversal_rejected(client):
    c, headers, ids = client
    r = c.post("/api/v1/documents/export",
               json=_spec("docx", ids["session_id"]), headers=headers)
    art_id = r.json()["artifact"]["id"]
    dl = c.get(f"/api/v1/documents/download/{art_id}", headers=headers)
    assert dl.status_code == 200
    assert "Content-SHA256" in dl.headers
    # 防穿越：篡改 storage_name 不会被接受（直接构造不存在的产物）
    # 用一条 storage_name 含 ../ 的伪造记录验证安全层拒绝
    from backend.app.models.entities import GeneratedArtifact
    session = c.app.state.session_factory()
    fake = GeneratedArtifact(
        kind="document", storage_name="../etc/passwd", mime_type="text/plain",
        size_bytes=1, sha256="a" * 64, contains_personal_data=False,
    )
    session.add(fake)
    session.commit()
    fid = fake.id
    bad = c.get(f"/api/v1/documents/download/{fid}", headers=headers)
    # safe_storage_path 应拒绝（400），或文件不存在（404）；不返回 200
    assert bad.status_code in (400, 404)


def test_inline_content_docx_path(client):
    """L4-D 前端路径：inline_content 直接导出，无需后端按 session 取数。"""
    c, headers, ids = client
    r = c.post("/api/v1/documents/export",
               json=_spec_inline("docx", SAMPLE_INLINE_ANSWER), headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["produced_format"] == "docx"
    art = body["artifact"]
    p = c.app.state.settings.exports_dir / art["storage_name"]
    data = p.read_bytes()
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    doc_xml = z.read("word/document.xml").decode("utf-8")
    # 中文正文与证据摘要应进入文档
    assert "初三" in doc_xml
    assert "听力薄弱" in doc_xml
    assert "0.62" in doc_xml


def test_inline_content_download_roundtrip(client):
    """inline_content 导出后下载，校验 mime 与中文可读。"""
    c, headers, ids = client
    r = c.post("/api/v1/documents/export",
               json=_spec_inline("docx", SAMPLE_INLINE_ANSWER), headers=headers)
    art_id = r.json()["artifact"]["id"]
    dl = c.get(f"/api/v1/documents/download/{art_id}", headers=headers)
    assert dl.status_code == 200
    assert dl.headers["Content-Type"].endswith("wordprocessingml.document")
    data = dl.content
    z = zipfile.ZipFile(io.BytesIO(data))
    assert "初三" in z.read("word/document.xml").decode("utf-8")


def test_inline_content_idempotent(client):
    """相同 inline_content 应幂等复用（前端重复点击不重复生成）。"""
    c, headers, ids = client
    spec = _spec_inline("docx", SAMPLE_INLINE_ANSWER)
    r1 = c.post("/api/v1/documents/export", json=spec, headers=headers)
    r2 = c.post("/api/v1/documents/export", json=spec, headers=headers)
    assert r2.json()["reused"] is True
    assert r2.json()["artifact"]["id"] == r1.json()["artifact"]["id"]

