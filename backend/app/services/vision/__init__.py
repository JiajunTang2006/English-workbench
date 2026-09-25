"""视觉 / 多模态服务包（方案 L3）。"""

from .analysis_service import VisionAnalysisService
from .provider import (
    ImageInput,
    ProviderCapabilities,
    RegionConfidence,
    VisionResult,
    VisionTask,
)
from .registry import get_provider, register_provider

__all__ = [
    "VisionAnalysisService",
    "VisionResult",
    "VisionTask",
    "ImageInput",
    "RegionConfidence",
    "ProviderCapabilities",
    "get_provider",
    "register_provider",
]
