"""通用 OpenAI 兼容文本模型适配器

实现 TextModelProvider 协议，接入任何 OpenAI 兼容 API 端点。
支持 DeepSeek、OpenRouter、CC Switch OpenAI 模式、第三方代理等。

与 DeepSeekTextProvider 的区别：
- 不假定特定模型名，完全由配置驱动
- 鉴权支持 Bearer token（标准）和自定义 header
- 模型能力可配置（不硬编码 context_length 等）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .base import (
    ModelCapabilities,
    ModelError,
    ModelErrorType,
    ModelResponse,
    ModelUsage,
    TextModelProvider,
    ToolCall,
)

logger = logging.getLogger(__name__)

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_MODELS_PATH = "/v1/models"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatTextProvider(TextModelProvider):
    """通用 OpenAI 兼容文本模型适配器。

    适用于任何提供 /v1/chat/completions 端点的 API：
    - DeepSeek (api.deepseek.com)
    - OpenRouter (openrouter.ai)
    - CC Switch OpenAI 模式
    - 其他 OpenAI 兼容代理
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_model: str = "gpt-4o",
        timeout: int = 120,
        max_retries: int = 3,
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer",
        extra_headers: dict[str, str] | None = None,
        context_length: int = 128_000,
        max_output_tokens: int = 4_096,
        supports_vision: bool = False,
        supports_tool_calls: bool = True,
        supports_json: bool = True,
        thinking_enabled: bool = True,
        reasoning_effort: str = "high",
        supports_reasoning: bool = False,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        # 接受 WorkBuddy 风格的完整 /v1/chat/completions 地址，也接受
        # 根地址或 /v1 基地址，避免重复拼接路径。
        if self._base_url.endswith("/chat/completions"):
            self._base_url = self._base_url[:-len("/chat/completions")].rstrip("/")
        self._chat_path = "/chat/completions" if self._base_url.endswith("/v1") else "/v1/chat/completions"
        self._models_path = "/models" if self._base_url.endswith("/v1") else "/v1/models"
        self._default_model = default_model
        self._timeout = timeout
        # 配置项表示“首次请求之后最多再重试几次”。
        # 0 仍会执行首次请求，1 则对应最多 2 次总尝试。
        self._max_attempts = max(0, int(max_retries)) + 1
        self._auth_header = auth_header
        self._auth_prefix = auth_prefix
        self._extra_headers = extra_headers or {}
        self._context_length = context_length
        self._max_output_tokens = max_output_tokens
        self._supports_vision = supports_vision
        self._supports_tool_calls = supports_tool_calls
        self._supports_json = supports_json
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        self._supports_reasoning = supports_reasoning
        self._client = None

    def _get_client(self):
        if self._client is None:
            import httpx
            headers = {
                self._auth_header: f"{self._auth_prefix} {self._api_key}".strip(),
                "Content-Type": "application/json",
            }
            headers.update(self._extra_headers)
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                headers=headers,
            )
        return self._client

    # ------------------------------------------------------------------
    # TextModelProvider 实现
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        used_model: str,
        temperature: float,
        max_tokens: int,
        response_format: dict | None,
        tools: list[dict] | None,
    ) -> dict[str, Any]:
        """构建请求体；供应商专用适配器可覆写协议方言。"""
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, self._max_output_tokens),
            "stream": False,
        }
        if response_format and self._supports_json:
            payload["response_format"] = response_format
        elif response_format:
            raise ModelError("所选模型不支持结构化输出，请选择兼容模型或普通对话。", ModelErrorType.INVALID_RESPONSE)
        if tools and not self._supports_tool_calls:
            raise ModelError("所选模型不支持工具调用，请使用普通对话或支持工具的模型。", ModelErrorType.INVALID_RESPONSE)
        if tools and self._supports_tool_calls:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self._thinking_enabled and self._supports_reasoning:
            payload["reasoning_effort"] = self._reasoning_effort
        return payload

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        used_model = model or self._default_model
        payload = self._build_payload(
            messages, used_model, temperature, max_tokens, response_format, tools,
        )

        data = await self._request_with_retry(payload)
        return self._parse_response(data, used_model)

    async def tool_loop(self, messages, tools, max_iterations=4):
        raise NotImplementedError("tool_loop 由 AgentLoop 驱动")

    async def estimate_cost(self, model, estimated_input_tokens, estimated_output_tokens):
        from ..cost import DEFAULT_PRICING
        pricing = DEFAULT_PRICING.get(model)
        if pricing is None:
            return 0.0
        input_cost = (estimated_input_tokens / 1_000_000) * pricing.input_price_per_million
        output_cost = (estimated_output_tokens / 1_000_000) * pricing.output_price_per_million
        return round(input_cost + output_cost, 4)

    def get_capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            supports_json=self._supports_json,
            supports_tool_calls=self._supports_tool_calls,
            supports_vision=self._supports_vision,
            supports_thinking=self._supports_reasoning,
            context_length=self._context_length,
            max_output_tokens=self._max_output_tokens,
        )

    async def list_models(self) -> list[str]:
        try:
            client = self._get_client()
            resp = await client.get(self._models_path)
            if resp.status_code != 200:
                return [self._default_model]
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            logger.warning("获取模型列表异常: %s", e)
            return [self._default_model]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _request_with_retry(self, payload: dict) -> dict:
        import httpx

        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await client.post(self._chat_path, json=payload)

                if resp.status_code in _RETRYABLE_STATUS:
                    if resp.status_code == 429:
                        last_error = ModelError(
                            "API 请求频率受限（HTTP 429），请稍后重试",
                            ModelErrorType.RATE_LIMIT,
                        )
                    else:
                        last_error = ModelError(
                            f"API 暂时不可用（HTTP {resp.status_code}）",
                            ModelErrorType.NETWORK,
                        )
                    if attempt < self._max_attempts:
                        wait = min(2 ** attempt, 10)
                        logger.warning(
                            "API %d，%ds 后重试 (%d/%d)",
                            resp.status_code, wait, attempt, self._max_attempts,
                        )
                        await asyncio.sleep(wait)
                    continue

                if resp.status_code == 401:
                    raise ModelError(
                        "API 鉴权失败：请检查 API Key",
                        ModelErrorType.AUTH_FAILED,
                    )

                if resp.status_code == 402:
                    # 兼容端点（含 DeepSeek）用 402 表示账户余额不足。
                    raise ModelError(
                        "API 余额不足（HTTP 402）：请前往服务商平台充值后重试",
                        ModelErrorType.INSUFFICIENT_BALANCE,
                    )

                if resp.status_code != 200:
                    error_body = resp.text[:500]
                    raise ModelError(
                        f"API 错误 {resp.status_code}: {error_body}",
                        ModelErrorType.INVALID_RESPONSE,
                    )

                return resp.json()

            except httpx.TimeoutException as e:
                last_error = ModelError(f"超时: {e}", ModelErrorType.TIMEOUT)
            except httpx.NetworkError as e:
                last_error = ModelError(f"网络错误: {e}", ModelErrorType.NETWORK)
            except ModelError:
                raise
            except Exception as e:
                last_error = ModelError(f"未知错误: {e}", ModelErrorType.UNKNOWN)

            if attempt < self._max_attempts:
                await asyncio.sleep(min(2 ** attempt, 10))

        raise last_error or ModelError("重试耗尽", ModelErrorType.UNKNOWN)

    def _parse_response(self, data: dict, model_name: str) -> ModelResponse:
        """解析 OpenAI 兼容格式的响应。"""
        choices = data.get("choices", [])
        if not choices:
            raise ModelError("API 返回空 choices", ModelErrorType.INVALID_RESPONSE)

        choice = choices[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")
        content = message.get("content") or ""

        tool_calls: list[ToolCall] = []
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                args_raw = func.get("arguments", "{}")
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
                tool_calls.append(ToolCall(
                    tool_name=func.get("name", ""),
                    arguments=args,
                ))

        usage_data = data.get("usage")
        usage = ModelUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
            provider_request_id=data.get("id"),
        ) if isinstance(usage_data, dict) and "prompt_tokens" in usage_data and "completion_tokens" in usage_data else None

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )
