"""Anthropic Claude 文本模型适配器

实现 TextModelProvider 协议，接入 Anthropic Messages API。
使用 Claude 原生 API 格式（POST /v1/messages）。

与 OpenAI 格式的主要差异：
- 鉴权：x-api-key header + anthropic-version header
- system 消息：不在 messages 数组中，而是顶级字段
- Tool calls：content 数组中的 tool_use block，结果用 tool_result block
- 响应：content 是数组，包含 text 和 tool_use 类型的 block

兼容 CC Switch 等代理工具的 Anthropic 模式。
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

_MESSAGES_PATH = "/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_RETRYABLE_STATUS = {429, 500, 502, 503, 529}


class AnthropicTextProvider(TextModelProvider):
    """Anthropic Claude 文本模型适配器，使用 Messages API 原生格式。

    兼容 CC Switch 等代理工具的 Anthropic 模式。
    支持自定义 base_url，可指向官方 API 或第三方代理。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        default_model: str = "claude-sonnet-4-20250514",
        timeout: int = 120,
        max_retries: int = 3,
        anthropic_version: str = _ANTHROPIC_VERSION,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout = timeout
        # 配置项表示“首次请求之后最多再重试几次”。
        self._max_attempts = max(0, int(max_retries)) + 1
        self._anthropic_version = anthropic_version
        self._client = None

    def _get_client(self):
        """延迟初始化 httpx AsyncClient。"""
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": self._anthropic_version,
                    "Content-Type": "application/json",
                },
            )
        return self._client

    # ------------------------------------------------------------------
    # TextModelProvider 实现
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        """调用 Anthropic Messages API。

        将 OpenAI 格式的 messages 转换为 Anthropic 格式：
        - 提取 system 消息到顶级字段
        - 合并连续的 user/assistant 消息
        - 转换 tool_calls / tool 角色消息为 content blocks
        """
        used_model = model or self._default_model
        payload = self._build_payload(
            messages=messages,
            model=used_model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )

        data = await self._request_with_retry(payload)
        return self._parse_response(data, used_model)

    async def tool_loop(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        max_iterations: int = 4,
    ) -> ModelResponse:
        raise NotImplementedError("tool_loop 由 AgentLoop 驱动")

    async def estimate_cost(
        self,
        model: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> float:
        from ..cost import DEFAULT_PRICING
        pricing = DEFAULT_PRICING.get(model)
        if pricing is None:
            return 0.0
        input_cost = (estimated_input_tokens / 1_000_000) * pricing.input_price_per_million
        output_cost = (estimated_output_tokens / 1_000_000) * pricing.output_price_per_million
        return round(input_cost + output_cost, 4)

    def get_capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            supports_json=True,
            supports_tool_calls=True,
            supports_vision=True,
            supports_thinking="thinking" in model or "opus" in model,
            context_length=200_000,
            max_output_tokens=8_192,
        )

    async def list_models(self) -> list[str]:
        return [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # 内部方法：请求构建与响应解析
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        """将 OpenAI 格式的 messages 转换为 Anthropic Messages API payload。

        转换规则：
        1. role=system 的消息提取到顶级 system 字段
        2. role=tool 的消息转换为 user 角色中的 tool_result content block
        3. assistant 消息中的 tool_calls 转换为 content 中的 tool_use block
        4. 合并连续同角色消息（Anthropic 要求 user/assistant 交替）
        """
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                if content:
                    system_parts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
                continue

            if role == "tool":
                # OpenAI 格式的工具结果消息 → Anthropic 的 user 消息中的 tool_result block
                tool_call_id = msg.get("tool_call_id", "")
                result_content = content
                if isinstance(result_content, str):
                    try:
                        result_content = json.loads(result_content)
                    except (json.JSONDecodeError, TypeError):
                        pass

                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": json.dumps(result_content, ensure_ascii=False) if not isinstance(result_content, str) else result_content,
                    }],
                })
                continue

            if role == "assistant" and msg.get("tool_calls"):
                # assistant 消息包含 tool_calls → 转换为 content blocks
                blocks: list[dict] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    args_raw = func.get("arguments", "{}")
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except (json.JSONDecodeError, TypeError):
                        args = {"_raw": args_raw}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": args,
                    })
                converted.append({"role": "assistant", "content": blocks})
                continue

            # 普通 user/assistant 消息
            if isinstance(content, str):
                converted.append({"role": role, "content": content})
            else:
                converted.append({"role": role, "content": json.dumps(content, ensure_ascii=False)})

        # 合并连续同角色消息（Anthropic 要求严格交替）
        merged = self._merge_consecutive_roles(converted)

        payload: dict[str, Any] = {
            "model": model,
            "messages": merged,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        # 转换 OpenAI tools 格式 → Anthropic tools 格式
        if tools:
            anthropic_tools = []
            for t in tools:
                func = t.get("function", {})
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })
            payload["tools"] = anthropic_tools
            payload["tool_choice"] = {"type": "auto"}

        return payload

    def _merge_consecutive_roles(self, messages: list[dict]) -> list[dict]:
        """合并连续同角色消息。

        Anthropic 要求 messages 中 user/assistant 严格交替。
        如果出现连续的同角色消息，将它们的 content 合并。
        """
        if not messages:
            return []

        result: list[dict] = [dict(messages[0])]

        for msg in messages[1:]:
            if msg["role"] == result[-1]["role"]:
                # 同角色，合并 content
                prev = result[-1]
                prev_content = prev.get("content")
                curr_content = msg.get("content")

                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    prev["content"] = prev_content + "\n\n" + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, list):
                    prev["content"] = prev_content + curr_content
                elif isinstance(prev_content, str) and isinstance(curr_content, list):
                    prev["content"] = [{"type": "text", "text": prev_content}] + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, str):
                    prev["content"] = prev_content + [{"type": "text", "text": curr_content}]
                else:
                    prev["content"] = str(prev_content) + "\n\n" + str(curr_content)
            else:
                result.append(dict(msg))

        return result

    # ------------------------------------------------------------------
    # 内部：HTTP 请求与响应解析
    # ------------------------------------------------------------------

    async def _request_with_retry(self, payload: dict) -> dict:
        """带指数退避重试的 HTTP POST。"""
        import httpx

        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await client.post(_MESSAGES_PATH, json=payload)

                if resp.status_code in _RETRYABLE_STATUS:
                    if resp.status_code == 429:
                        last_error = ModelError(
                            "Anthropic API 请求频率受限（HTTP 429），请稍后重试",
                            ModelErrorType.RATE_LIMIT,
                        )
                    else:
                        last_error = ModelError(
                            f"Anthropic API 暂时不可用（HTTP {resp.status_code}）",
                            ModelErrorType.NETWORK,
                        )
                    if attempt < self._max_attempts:
                        wait = min(2 ** attempt, 10)
                        logger.warning(
                            "Anthropic API %d，%ds 后重试 (%d/%d)",
                            resp.status_code, wait, attempt, self._max_attempts,
                        )
                        await asyncio.sleep(wait)
                    continue

                if resp.status_code == 401:
                    raise ModelError(
                        "Anthropic API 鉴权失败：请检查 API Key",
                        ModelErrorType.AUTH_FAILED,
                    )

                if resp.status_code != 200:
                    error_body = resp.text[:500]
                    raise ModelError(
                        f"Anthropic API 错误 {resp.status_code}: {error_body}",
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
        """解析 Anthropic Messages API 响应。

        Anthropic 响应格式：
        {
          "content": [
            {"type": "text", "text": "..."},
            {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
          ],
          "stop_reason": "end_turn" | "tool_use" | "max_tokens",
          "usage": {"input_tokens": N, "output_tokens": N}
        }
        """
        content_blocks = data.get("content", [])
        if not content_blocks:
            raise ModelError("API 返回空 content", ModelErrorType.INVALID_RESPONSE)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            block_type = block.get("type", "")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(
                    tool_name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        # stop_reason 映射
        stop_reason = data.get("stop_reason", "end_turn")
        finish_reason = "tool_calls" if stop_reason == "tool_use" else "stop"
        if stop_reason == "max_tokens":
            finish_reason = "length"

        usage_data = data.get("usage", {})
        usage = ModelUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            provider_request_id=data.get("id"),
        )

        return ModelResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )
