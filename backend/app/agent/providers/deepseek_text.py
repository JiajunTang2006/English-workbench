"""
DeepSeek 文本模型适配器

实现 TextModelProvider 协议，接入 DeepSeek API。
使用 OpenAI 兼容 API 格式（POST https://api.deepseek.com/chat/completions）。

功能：
- Bearer 鉴权
- 可配置 Base URL 和模型名
- JSON 输出（response_format={'type': 'json_object'}）
- Tool Calls（function calling）
- 超时、有限重试和错误分类
- usage 记录

安全：
- API Key 只从构造函数传入（来源为环境变量）
- 不保存 API Key 到日志或数据库
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

_CHAT_COMPLETIONS_PATH = "/chat/completions"
_MODELS_PATH = "/models"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _decode_tool_arguments(raw: Any) -> dict[str, Any] | None:
    """Decode tool-call arguments, tolerating a truncated final brace.

    DeepSeek occasionally returns a valid JSON object whose final ``}`` is
    omitted when a large structured report is emitted.  We only repair the
    unambiguous case (balanced strings, no trailing garbage, and missing
    closing braces/brackets); all other malformed payloads remain rejected.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    # Locate the object start and scan outside quoted strings so braces in
    # Chinese text do not affect the balance calculation.
    start = text.find("{")
    if start < 0:
        return None
    candidate = text[start:]
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or (char == "}" and stack[-1] != "{") or (char == "]" and stack[-1] != "["):
                return None
            stack.pop()
    if in_string or not stack:
        return None
    repaired = candidate.rstrip()
    # Only close still-open containers; do not try to rewrite arbitrary JSON.
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    try:
        value = json.loads(repaired)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


class DeepSeekTextProvider(TextModelProvider):
    """DeepSeek 文本模型适配器，使用 OpenAI 兼容 API。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        default_model: str = "deepseek-chat",
        timeout: int = 120,
        max_retries: int = 3,
        thinking_enabled: bool = True,
        reasoning_effort: str = "high",
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout = timeout
        # 配置项表示“首次请求之后最多再重试几次”。
        self._max_attempts = max(0, int(max_retries)) + 1
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort if reasoning_effort in {"high", "max", "off"} else "high"
        self._client = None

    def _get_client(self):
        """延迟初始化 httpx AsyncClient。"""
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        """调用 DeepSeek Chat Completions API。"""
        used_model = model or self._default_model
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # 关闭思考时只发送 thinking.disabled，不能携带 reasoning_effort。
        payload["thinking"] = {"type": "enabled" if self._thinking_enabled else "disabled"}
        if self._thinking_enabled:
            payload["reasoning_effort"] = self._reasoning_effort
        if response_format:
            payload["response_format"] = response_format
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = await self._request_with_retry(payload)
        return self._parse_response(data, used_model)

    async def tool_loop(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        max_iterations: int = 4,
    ) -> ModelResponse:
        """工具调用循环由 AgentLoop 驱动，此方法不在此实现。"""
        raise NotImplementedError("tool_loop 由 AgentLoop 驱动")

    async def estimate_cost(
        self,
        model: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> float:
        """估算调用成本（CNY）。"""
        from ..cost import DEFAULT_PRICING
        pricing = DEFAULT_PRICING.get(model)
        if pricing is None:
            return 0.0
        input_cost = (estimated_input_tokens / 1_000_000) * pricing.input_price_per_million
        output_cost = (estimated_output_tokens / 1_000_000) * pricing.output_price_per_million
        return round(input_cost + output_cost, 4)

    def get_capabilities(self, model: str) -> ModelCapabilities:
        """获取 DeepSeek 模型能力。"""
        return ModelCapabilities(
            supports_json=True,
            supports_tool_calls=True,
            supports_vision=False,
            supports_thinking="reasoner" in model or "pro" in model,
            context_length=1_000_000,
            max_output_tokens=384_000,
        )

    async def list_models(self) -> list[str]:
        """获取 DeepSeek 可用模型列表。"""
        try:
            client = self._get_client()
            resp = await client.get(_MODELS_PATH)
            if resp.status_code != 200:
                return ["deepseek-chat", "deepseek-reasoner"]
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            logger.warning("获取模型列表异常: %s", e)
            return ["deepseek-chat", "deepseek-reasoner"]

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _request_with_retry(self, payload: dict) -> dict:
        """带指数退避重试的 HTTP POST。"""
        import httpx

        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await client.post(_CHAT_COMPLETIONS_PATH, json=payload)

                if resp.status_code in _RETRYABLE_STATUS:
                    if resp.status_code == 429:
                        last_error = ModelError(
                            "DeepSeek API 请求频率受限（HTTP 429），请稍后重试",
                            ModelErrorType.RATE_LIMIT,
                        )
                    else:
                        last_error = ModelError(
                            f"DeepSeek API 暂时不可用（HTTP {resp.status_code}）",
                            ModelErrorType.NETWORK,
                        )
                    if attempt < self._max_attempts:
                        wait = min(2 ** attempt, 10)
                        logger.warning(
                            "DeepSeek API %d，%ds 后重试 (%d/%d)",
                            resp.status_code, wait, attempt, self._max_attempts,
                        )
                        await asyncio.sleep(wait)
                    continue

                if resp.status_code == 401:
                    raise ModelError(
                        "DeepSeek API 鉴权失败：请检查 API Key",
                        ModelErrorType.AUTH_FAILED,
                    )

                if resp.status_code == 402:
                    # 平台返回 402 表示账户余额不足（Key 本身有效、鉴权已通过）。
                    raise ModelError(
                        "DeepSeek API 余额不足（HTTP 402）：请前往 DeepSeek 开放平台充值后重试",
                        ModelErrorType.INSUFFICIENT_BALANCE,
                    )

                if resp.status_code != 200:
                    error_body = resp.text[:500]
                    raise ModelError(
                        f"DeepSeek API 错误 {resp.status_code}: {error_body}",
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
        """解析 DeepSeek API 响应。"""
        choices = data.get("choices", [])
        if not choices:
            raise ModelError("API 返回空 choices", ModelErrorType.INVALID_RESPONSE)

        choice = choices[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")
        content = message.get("content") or ""
        reasoning_content = message.get("reasoning_content")
        if reasoning_content is not None:
            reasoning_content = str(reasoning_content)

        # 解析 tool_calls（OpenAI 兼容格式）
        tool_calls: list[ToolCall] = []
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                args_raw = func.get("arguments", "{}")
                args = _decode_tool_arguments(args_raw)
                if args is None:
                    args = {"_raw": args_raw}
                tool_calls.append(ToolCall(
                    tool_name=func.get("name", ""),
                    arguments=args,
                ))

        # 解析 usage
        usage_data = data.get("usage", {})
        usage = ModelUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
            provider_request_id=data.get("id"),
        )

        return ModelResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )
