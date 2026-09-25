"""多模态能力路由（方案 §10.7 L3-D）。

根据文件类型、任务与模型能力选择：本地解析 / OCR / 视觉 / 纯文本。
无视觉 Provider 时 fail-closed；文本任务不产生无必要视觉费用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .provider import VisionTask
from .registry import get_provider

Modality = Literal["text", "image", "scanned_pdf", "native_pdf"]


@dataclass
class RouteDecision:
    modality: Modality
    use_vision: bool
    use_local_parse: bool
    provider_name: str | None
    reason: str


TEXT_EXT = {".txt", ".md", ".csv", ".xlsx", ".docx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
PDF_EXT = {".pdf"}


def classify_file(original_name: str) -> Modality:
    ext = original_name.lower().rsplit(".", 1)[-1] if "." in original_name else ""
    if f".{ext}" in TEXT_EXT:
        return "text"
    if f".{ext}" in IMAGE_EXT:
        return "image"
    if f".{ext}" in PDF_EXT:
        return "scanned_pdf"  # 默认按扫描件处理，本地有文本则降级
    return "text"


def route(
    *,
    original_name: str,
    task: VisionTask,
    vision_enabled: bool,
    has_local_text: bool = False,
) -> RouteDecision:
    """决定处理路径。

    - 纯文本文件且已有本地解析 → 本地解析，不调用视觉（不产生费用）。
    - 图片/扫描 PDF → 需要视觉；无 Provider 或未开启 → fail-closed 报错。
    """
    modality = classify_file(original_name)

    if modality == "text":
        return RouteDecision(
            modality=modality, use_vision=False, use_local_parse=True,
            provider_name=None, reason="text_file_local_parse",
        )

    # image / scanned_pdf 需要视觉能力
    if not vision_enabled:
        return RouteDecision(
            modality=modality, use_vision=False, use_local_parse=False,
            provider_name=None, reason="vision_disabled_fail_closed",
        )

    provider = get_provider()
    if provider is None:
        return RouteDecision(
            modality=modality, use_vision=False, use_local_parse=False,
            provider_name=None, reason="no_vision_provider_fail_closed",
        )

    # 扫描 PDF 若本地已提取文本，优先本地（省钱）
    if modality == "scanned_pdf" and has_local_text:
        return RouteDecision(
            modality=modality, use_vision=False, use_local_parse=True,
            provider_name=None, reason="pdf_has_local_text",
        )

    return RouteDecision(
        modality=modality, use_vision=True, use_local_parse=False,
        provider_name=provider.capabilities.name,
        reason="vision_required",
    )
