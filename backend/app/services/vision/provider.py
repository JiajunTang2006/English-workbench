"""视觉 Provider 抽象与结果契约（方案 §10.5 / §10.6）。

Provider 不直接写数据库：统一服务层负责状态、幂等、审计与证据。
真实 Provider（如云端 OCR/视觉 API）在 L3-B 接入；本模块提供 Protocol、
数据类与能力声明，使链路在无外部 API 时也能 fail-closed 运行与测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

VisionTask = Literal["ocr", "layout", "handwriting", "exam_understanding"]


@dataclass
class ImageInput:
    """单张输入图像（本地路径，不携带 Base64 原文到日志）。"""

    path: str
    page_no: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class RegionConfidence:
    """单块识别区域（含坐标与置信度）。"""

    text: str
    bbox: list[float]  # [x1, y1, x2, y2]，归一化 0..1
    confidence: float | None = None
    kind: str | None = None  # text/table/question/illigible


@dataclass
class VisionResult:
    """Provider 必须返回的结构化结果（方案 §10.5）。"""

    provider: str
    model_name: str | None
    request_id: str | None
    pages: list[dict[str, Any]] = field(default_factory=list)  # 页级文本/结构
    regions: list[RegionConfidence] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    low_confidence: list[dict[str, Any]] = field(default_factory=list)  # 低置信/不可识别区
    cost_yuan: float | None = None
    safety_filtered: bool = False
    error: str | None = None
    raw_size_bytes: int | None = None


@dataclass
class ProviderCapabilities:
    """模型能力声明（方案 §10.6）。"""

    name: str
    supports_image_input: bool = False
    supports_pdf_input: bool = False
    supports_structured_output: bool = False
    supports_handwriting: bool = False
    max_images_per_request: int = 1
    max_image_bytes: int = 0
    max_total_pixels: int = 0
    data_retention_mode: str = "unknown"
    price_per_page_yuan: float | None = None
    price_per_image_yuan: float | None = None


@runtime_checkable
class VisionProvider(Protocol):
    """视觉 Provider 契约（方案 §10.5）。"""

    capabilities: ProviderCapabilities

    async def analyze(
        self,
        *,
        images: list[ImageInput],
        task: VisionTask,
        response_schema: dict[str, Any],
    ) -> VisionResult:
        """分析图像并返回 VisionResult。"""
        ...


def result_to_content_json(result: VisionResult) -> dict[str, Any]:
    """将 VisionResult 收敛为可入库的 content_json（去除原始大字段）。"""
    return {
        "provider": result.provider,
        "model_name": result.model_name,
        "request_id": result.request_id,
        "pages": result.pages,
        "regions": [
            {"text": r.text, "bbox": r.bbox, "confidence": r.confidence, "kind": r.kind}
            for r in result.regions
        ],
        "tables": result.tables,
        "low_confidence": result.low_confidence,
        "cost_yuan": result.cost_yuan,
        "safety_filtered": result.safety_filtered,
        "error": result.error,
    }
