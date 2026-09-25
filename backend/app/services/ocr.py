"""统一 OCR 门面。

OCR 不直接保存数据库状态；它把视觉 Provider 的结果转换成稳定结构，
供附件分析和试卷录入复用。教师确认前的结果不得晋升为正式资料。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .vision.provider import ImageInput, VisionResult


class OCRService:
    def __init__(self, vision_provider=None) -> None:
        self._provider = vision_provider

    async def recognize_async(self, image_paths: list[str], *, handwriting: bool = False, table: bool = False) -> dict[str, Any]:
        if self._provider is None:
            return {"text": "", "pages": [], "tables": [], "confidence": 0.0, "error": "ocr_provider_not_configured"}
        task = "handwriting" if handwriting else ("layout" if table else "ocr")
        images = [ImageInput(path=str(Path(path)), page_no=index + 1) for index, path in enumerate(image_paths)]
        try:
            if hasattr(self._provider, "analyze"):
                result = await self._provider.analyze(images=images, task=task, response_schema={"type": "object"})
                return self._from_vision(result)
            response = await self._provider.analyze_images([str(path) for path in image_paths], "请识别图片中的文字和表格，仅返回可见内容。", max_tokens=4096)
            return self._from_legacy(response)
        except Exception as exc:
            return {"text": "", "pages": [], "tables": [], "confidence": 0.0, "error": f"ocr_failed:{type(exc).__name__}"}

    def recognize_text(
        self,
        image_urls: list[str],
        *,
        handwriting: bool = False,
    ) -> dict[str, Any]:
        return self._run(self.recognize_async(image_urls, handwriting=handwriting))

    def recognize_table(
        self,
        image_urls: list[str],
    ) -> dict[str, Any]:
        result = self._run(self.recognize_async(image_urls, table=True))
        return {"tables": result.get("tables", []), "confidence": result.get("confidence", 0.0), "error": result.get("error")}

    def recognize_handwriting(
        self,
        image_urls: list[str],
    ) -> dict[str, Any]:
        return self.recognize_text(image_urls, handwriting=True)

    @staticmethod
    def _run(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        return {"text": "", "pages": [], "tables": [], "confidence": 0.0, "error": "ocr_sync_called_inside_event_loop"}

    @staticmethod
    def _from_vision(result: VisionResult) -> dict[str, Any]:
        pages = result.pages or []
        text = "\n".join(str(page.get("text", "")) for page in pages if isinstance(page, dict))
        return {"text": text, "pages": pages, "tables": result.tables or [], "confidence": 1.0 if text else 0.0,
                "low_confidence": result.low_confidence or [], "request_id": result.request_id, "error": result.error}

    @staticmethod
    def _from_legacy(response) -> dict[str, Any]:
        text = getattr(response, "content", "") or ""
        return {"text": text, "pages": [{"page_no": 1, "text": text}] if text else [], "tables": [],
                "confidence": 1.0 if text else 0.0, "request_id": getattr(getattr(response, "usage", None), "provider_request_id", None), "error": None}
