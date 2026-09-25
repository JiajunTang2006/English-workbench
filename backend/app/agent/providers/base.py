"""
模型供应商协议抽象

定义文本和视觉模型的统一接口，使供应商可配置可替换。

协议：
- TextModelProvider: 文本模型（Agent 循环、报告生成）
- VisionModelProvider: 视觉模型（图片理解）
- ModelCapabilities: 能力声明
- ModelUsage: 用量记录
- ModelError: 错误分类

不假定 DeepSeek 文本模型支持图片。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelErrorType(str, Enum):
    """模型错误类型分类。"""
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_FAILED = "auth_failed"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    INVALID_RESPONSE = "invalid_response"
    NETWORK = "network"
    UNKNOWN = "unknown"


class ModelError(Exception):
    """模型调用异常。"""

    def __init__(self, message: str, error_type: ModelErrorType = ModelErrorType.UNKNOWN):
        super().__init__(message)
        self.error_type = error_type


@dataclass
class ModelCapabilities:
    """模型能力声明。"""
    supports_json: bool = False
    supports_tool_calls: bool = False
    supports_vision: bool = False
    supports_thinking: bool = False
    context_length: int = 4096
    max_output_tokens: int = 2048


@dataclass
class ModelUsage:
    """模型用量记录。"""
    input_tokens: int = 0
    output_tokens: int = 0
    provider_request_id: str | None = None
    cost_yuan: float = 0.0


@dataclass
class ToolCall:
    """模型发出的工具调用请求。"""
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResponse:
    """模型响应。"""
    content: str = ""
    # DeepSeek thinking 模式会在 assistant 消息中返回这段隐藏推理。
    # 继续发送 tool call 对话时，API 要求原样带回 reasoning_content；
    # 统一放在响应对象中，避免供应商适配层丢失该协议字段。
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: ModelUsage | None = None
    finish_reason: str = "stop"
    # stop / tool_calls / length / error


class TextModelProvider:
    """文本模型供应商抽象接口。

    第一版实现 DeepSeek 文本适配器。
    本地 PDF 提取、OCR 和 Excel 解析不经过模型供应商。
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        """文本补全（可选工具调用）。"""
        raise NotImplementedError

    async def tool_loop(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        max_iterations: int = 4,
    ) -> ModelResponse:
        """工具调用循环（模型-工具-模型）。

        最大迭代次数由调用方控制，默认 4 次。
        """
        raise NotImplementedError

    async def estimate_cost(
        self,
        model: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> float:
        """估算调用成本（CNY）。"""
        raise NotImplementedError

    def get_capabilities(self, model: str) -> ModelCapabilities:
        """获取模型能力声明。"""
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        """获取可用模型列表。"""
        raise NotImplementedError


class VisionModelProvider:
    """视觉模型供应商抽象接口。

    供应商可配置，接口只接收脱敏派生图。
    不开启视觉开关时使用本地 OCR 降级。
    """

    async def analyze_images(
        self,
        images: list[str],  # 脱敏派生图路径
        prompt: str,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        """分析图片内容。"""
        raise NotImplementedError

    async def estimate_cost(
        self,
        model: str,
        num_images: int,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> float:
        """估算视觉调用成本（CNY）。"""
        raise NotImplementedError

    def get_capabilities(self, model: str) -> ModelCapabilities:
        """获取模型能力声明。"""
        raise NotImplementedError
