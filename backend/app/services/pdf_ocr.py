"""Complete scanned PDF pages with the explicitly enabled vision provider."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

from .pdf_render import available_renderer, render_pdf_page


MAX_OCR_PAGES_PER_DOCUMENT = 20


def complete_scanned_pdf(path: Path, parsed: dict[str, Any], *,
                         should_cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Keep extracted pages on failure; never present missing OCR as success."""
    if parsed.get("format") != "pdf" or not parsed.get("needs_ocr"):
        return parsed
    metadata = dict(parsed.get("metadata") or {})
    pending = list(metadata.get("ocr_pages") or [])
    if not pending:
        return parsed
    if len(pending) > MAX_OCR_PAGES_PER_DOCUMENT:
        metadata["ocr_unavailable_reason"] = "扫描页超过单次识别上限，请拆分文件"
        parsed["metadata"] = metadata
        return parsed

    from ..agent.config import get_agent_config
    config = get_agent_config()
    if not (config.feature_flags.get("vision_analysis_enabled")
            and config.vision_provider and config.vision_api_key_configured):
        metadata["ocr_unavailable_reason"] = "尚未启用并配置视觉文字识别"
        parsed["metadata"] = metadata
        return parsed
    if available_renderer() is None:
        metadata["ocr_unavailable_reason"] = "PDF 页面渲染组件不可用"
        parsed["metadata"] = metadata
        return parsed

    from .ocr import OCRService
    from .vision.registry import get_provider
    provider = get_provider(config.vision_provider)
    if provider is None or provider.capabilities.name == "local_stub":
        metadata["ocr_unavailable_reason"] = "视觉文字识别服务不可用"
        parsed["metadata"] = metadata
        return parsed

    pages = list(parsed.get("pages") or [])
    unresolved: list[int] = []
    with tempfile.TemporaryDirectory(prefix="teachmate-pdf-ocr-") as tmp:
        for index, page_no in enumerate(pending):
            if should_cancel is not None and should_cancel():
                unresolved.extend(pending[index:])
                break
            if not isinstance(page_no, int) or page_no < 1 or page_no > len(pages):
                unresolved.append(page_no)
                continue
            try:
                image = render_pdf_page(path, page_no, temp_dir=Path(tmp))
                if image is None:
                    unresolved.append(page_no)
                    continue
                ocr = OCRService(provider).recognize_text([str(image)])
                text = str(ocr.get("text") or "").strip()
                if ocr.get("error") or not text:
                    unresolved.append(page_no)
                    continue
                pages[page_no - 1]["text"] = text
                pages[page_no - 1]["needs_ocr"] = False
                pages[page_no - 1]["ocr_provider"] = provider.capabilities.name
                pages[page_no - 1]["ocr_low_confidence"] = ocr.get("low_confidence") or []
                if ocr.get("tables"):
                    pages[page_no - 1]["tables"] = ocr["tables"]
            except Exception:
                # Vision providers may raise transport or timeout errors. Keep
                # the page pending so one failed request does not discard the
                # text already extracted from other pages.
                unresolved.append(page_no)

    parsed["pages"] = pages
    parsed["content"] = "\n\n--- Page Break ---\n\n".join(
        str(page.get("text") or "") for page in pages)
    parsed["needs_ocr"] = bool(unresolved)
    metadata["ocr_pages"] = unresolved
    metadata["ocr_completed_pages"] = [number for number in pending if number not in unresolved]
    if unresolved:
        metadata["ocr_unavailable_reason"] = "部分扫描页识别失败，请检查识别服务或拆分文件重试"
    parsed["metadata"] = metadata
    return parsed
