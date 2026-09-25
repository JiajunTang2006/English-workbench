"""智谱 GLM / Z.AI Provider。

智谱国内开放平台与 Z.AI 都提供 OpenAI 兼容接口，但模型与参数并不完全
相同。这里集中处理 ``thinking``、GLM-5.3 的 ``reasoning_effort`` 以及
``/chat/completions`` 路径，避免把供应商差异散落到通用 Provider 中。
"""

from __future__ import annotations

import re
from typing import Any

from .openai_compat_text import OpenAICompatTextProvider

ZHIPU_BIGMODEL_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
ZHIPU_DEFAULT_BASE_URL = ZHIPU_BIGMODEL_BASE_URL
ZHIPU_DEFAULT_MODEL = "glm-5.3-flash"

_BIGMODEL_API_KEY_RE = re.compile(
    r"^[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}$"
)


def zhipu_key_is_bigmodel(api_key: str) -> bool:
    """Return whether a credential has the documented BigModel ``id.secret`` shape."""
    return bool(_BIGMODEL_API_KEY_RE.fullmatch((api_key or "").strip()))


def resolve_zhipu_base_url(base_url: str, api_key: str) -> str:
    """Keep BigModel credentials away from the separate Z.AI credential domain.

    Older saved profiles used the Z.AI URL as their default even in the Chinese
    BigModel build.  BigModel credentials are distinguishable by their documented
    ``id.secret`` form, so an untouched Z.AI default can be migrated safely before
    the credential is sent over the network.  Explicit custom endpoints are kept.
    """
    normalized = (base_url or ZHIPU_DEFAULT_BASE_URL).strip().rstrip("/")
    if zhipu_key_is_bigmodel(api_key):
        if normalized == ZHIPU_ZAI_BASE_URL:
            return ZHIPU_BIGMODEL_BASE_URL
        if normalized == f"{ZHIPU_ZAI_BASE_URL}/chat/completions":
            return f"{ZHIPU_BIGMODEL_BASE_URL}/chat/completions"
    return normalized


def zhipu_model_is_glm53(model_name: str) -> bool:
    """GLM-5.3 系列使用 Z.AI 新版推理参数约束。"""
    return (model_name or "").strip().lower().startswith("glm-5.3")


def zhipu_model_supports_thinking(model_name: str) -> bool:
    """智谱仅对 GLM-4.5 及以上推理模型开放 thinking 参数。"""
    model = (model_name or "").lower()
    return any(marker in model for marker in ("glm-4.5", "glm-4.6", "glm-4.7", "glm-5"))


class ZhipuTextProvider(OpenAICompatTextProvider):
    """调用智谱 GLM Chat Completions 的文本 Provider。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = ZHIPU_DEFAULT_BASE_URL,
        default_model: str = ZHIPU_DEFAULT_MODEL,
        thinking_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        base_url = resolve_zhipu_base_url(base_url, api_key)
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            thinking_enabled=thinking_enabled,
            supports_reasoning=True,
            **kwargs,
        )
        # 智谱 v4 的 base URL 本身不带 /v1。
        self._chat_path = "/chat/completions"
        self._models_path = "/models"

    def _build_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = super()._build_payload(*args, **kwargs)
        model_name = str(payload["model"])
        reasoning_effort = payload.pop("reasoning_effort", None)
        if zhipu_model_supports_thinking(model_name) and self._thinking_enabled:
            payload["thinking"] = {"type": "enabled"}
            if zhipu_model_is_glm53(model_name):
                # GLM-5.3 / 5.3-Flash 官方只接受 low、high、max。
                payload["reasoning_effort"] = (
                    reasoning_effort if reasoning_effort in {"low", "high", "max"} else "high"
                )
        return payload
