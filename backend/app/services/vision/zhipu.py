"""智谱 GLM 视觉 Provider。

智谱视觉请求沿用官方 OpenAI 兼容的 ``content`` 数组：文本段和
``image_url`` 段放在同一条 user message 中。
"""

from __future__ import annotations

from .openai_compat import OpenAICompatibleVisionProvider
from ...agent.providers.zhipu import (
    ZHIPU_DEFAULT_BASE_URL,
    resolve_zhipu_base_url,
    zhipu_model_supports_thinking,
)



class ZhipuVisionProvider(OpenAICompatibleVisionProvider):
    """调用智谱 GLM 多模态 Chat Completions 的视觉 Provider。"""

    def __init__(self, *, api_key: str, base_url: str, model_name: str, **kwargs) -> None:
        super().__init__(
            api_key=api_key,
            base_url=resolve_zhipu_base_url(base_url, api_key),
            model_name=model_name,
            endpoint_path="/chat/completions",
            thinking_enabled=zhipu_model_supports_thinking(model_name),
            **kwargs,
        )
        self.capabilities.name = "zhipu_vision"
