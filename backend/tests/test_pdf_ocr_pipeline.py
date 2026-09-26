from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from backend.app.services.document_parser import DocumentParser
from backend.app.services.pdf_ocr import complete_scanned_pdf


def test_image_only_pdf_enters_ocr_state(tmp_path):
    from PIL import Image

    path = tmp_path / "scanned.pdf"
    Image.new("RGB", (300, 300), color="white").save(path, "PDF")
    result = DocumentParser().parse(path)
    assert result["error"] is None
    assert result["needs_ocr"] is True
    assert result["metadata"]["ocr_pages"] == [1]


def test_scanned_page_is_not_marked_as_parsed(monkeypatch, tmp_path):
    class Page:
        images = [{"src": "scan"}]

        def extract_text(self):
            return ""

        def extract_tables(self):
            return []

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setitem(sys.modules, "pdfplumber",
                        SimpleNamespace(open=lambda _: Pdf()))
    result = DocumentParser()._parse_pdf(tmp_path / "scan.pdf")
    assert result["needs_ocr"] is True
    assert result["metadata"]["ocr_pages"] == [1]
    assert result["pages"][0]["needs_ocr"] is True


def test_configured_vision_ocr_completes_scanned_page(monkeypatch, tmp_path):
    from backend.app.agent import config as agent_config
    from backend.app.services import pdf_ocr
    from backend.app.services.ocr import OCRService
    from backend.app.services.vision import registry

    fake_config = SimpleNamespace(
        feature_flags={"vision_analysis_enabled": True},
        vision_provider="fake", vision_api_key_configured=True,
    )
    provider = SimpleNamespace(capabilities=SimpleNamespace(name="fake_ocr"))
    monkeypatch.setattr(agent_config, "get_agent_config", lambda: fake_config)
    monkeypatch.setattr(registry, "get_provider", lambda _: provider)
    # 统一页面渲染服务：优先 PDFium，回退 pdftoppm；测试直接替换渲染函数。
    monkeypatch.setattr(pdf_ocr, "available_renderer", lambda: "pdfium")

    def render(path, page_no, *, temp_dir, dpi=150):
        image = Path(temp_dir) / f"page-{page_no}.jpg"
        image.write_bytes(b"fake-image")
        return image

    monkeypatch.setattr(pdf_ocr, "render_pdf_page", render)
    monkeypatch.setattr(OCRService, "recognize_text",
                        lambda *_: {"text": "第一题 阅读", "error": None})
    parsed = {
        "format": "pdf", "needs_ocr": True, "content": "",
        "pages": [{"page": 1, "text": "", "needs_ocr": True}],
        "metadata": {"ocr_pages": [1]},
    }
    result = complete_scanned_pdf(tmp_path / "scan.pdf", parsed)
    assert result["needs_ocr"] is False
    assert result["content"] == "第一题 阅读"
    assert result["metadata"]["ocr_completed_pages"] == [1]


def test_vision_service_failure_keeps_page_pending(monkeypatch, tmp_path):
    from backend.app.agent import config as agent_config
    from backend.app.services import pdf_ocr
    from backend.app.services.ocr import OCRService
    from backend.app.services.vision import registry

    monkeypatch.setattr(agent_config, "get_agent_config", lambda: SimpleNamespace(
        feature_flags={"vision_analysis_enabled": True},
        vision_provider="fake", vision_api_key_configured=True,
    ))
    monkeypatch.setattr(registry, "get_provider", lambda _: SimpleNamespace(
        capabilities=SimpleNamespace(name="fake_ocr"),
    ))
    monkeypatch.setattr(pdf_ocr, "available_renderer", lambda: "pdfium")

    def render(path, page_no, *, temp_dir, dpi=150):
        image = Path(temp_dir) / f"page-{page_no}.jpg"
        image.write_bytes(b"fake-image")
        return image

    def fail_ocr(*_):
        raise TimeoutError("vision request timed out")

    monkeypatch.setattr(pdf_ocr, "render_pdf_page", render)
    monkeypatch.setattr(OCRService, "recognize_text", fail_ocr)
    parsed = {
        "format": "pdf", "needs_ocr": True, "content": "已提取文字",
        "pages": [{"page": 1, "text": "已提取文字", "needs_ocr": True}],
        "metadata": {"ocr_pages": [1]},
    }
    result = complete_scanned_pdf(tmp_path / "scan.pdf", parsed)
    assert result["needs_ocr"] is True
    assert result["content"] == "已提取文字"
    assert result["metadata"]["ocr_pages"] == [1]
