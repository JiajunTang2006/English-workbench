"""Deterministic local text provider for integration tests.

This provider is intentionally not wired into the production default factory.
It gives runtime and error-path tests a real ``TextModelProvider`` without
requiring a paid API key or sending any data outside the process.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .base import (
    ModelCapabilities,
    ModelError,
    ModelErrorType,
    ModelResponse,
    ModelUsage,
    TextModelProvider,
)


@dataclass(frozen=True)
class FakeProviderStep:
    """One deterministic response or classified provider failure."""

    response: ModelResponse | None = None
    error_type: ModelErrorType | None = None
    error_message: str = "模拟 Provider 错误"


class FakeTextProvider(TextModelProvider):
    """Scripted provider used by local integration tests.

    ``steps`` are consumed in order. A step may be a ``ModelResponse``, a
    ``FakeProviderStep`` or a callable receiving the request payload and
    returning either of those values. Every request is retained in
    ``calls`` for assertions; the provider never logs or transmits messages.
    """

    def __init__(
        self,
        steps: Iterable[
            ModelResponse | FakeProviderStep | Callable[[dict[str, Any]], Any]
        ] = (),
        *,
        default_model: str = "fake-model",
        capabilities: ModelCapabilities | None = None,
        cost_yuan_per_1k_tokens: float = 0.0,
    ) -> None:
        self._steps = list(steps)
        self._default_model = default_model
        self._capabilities = capabilities or ModelCapabilities(
            supports_json=True,
            supports_tool_calls=True,
            context_length=128_000,
            max_output_tokens=8_192,
        )
        self._cost_yuan_per_1k_tokens = max(0.0, cost_yuan_per_1k_tokens)
        self.calls: list[dict[str, Any]] = []
        self._call_index = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        request = {
            "messages": messages,
            "model": model or self._default_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
            "tools": tools,
        }
        self.calls.append(request)

        if self._call_index >= len(self._steps):
            return ModelResponse(
                content="模拟 Provider 默认响应",
                usage=ModelUsage(provider_request_id=f"fake-{self._call_index + 1}"),
            )

        step = self._steps[self._call_index]
        self._call_index += 1
        if callable(step):
            step = step(request)
        if isinstance(step, FakeProviderStep):
            if step.error_type is not None:
                raise ModelError(step.error_message, step.error_type)
            step = step.response
        if isinstance(step, Exception):
            raise step
        if not isinstance(step, ModelResponse):
            raise ModelError(
                "模拟 Provider 返回了无效响应",
                ModelErrorType.INVALID_RESPONSE,
            )
        if step.usage is None:
            step.usage = ModelUsage(
                provider_request_id=f"fake-{self._call_index}",
            )
        return step

    async def tool_loop(self, messages, tools, max_iterations=4):
        raise NotImplementedError("AgentLoop 负责执行模拟 Provider 的工具循环")

    async def estimate_cost(self, model, estimated_input_tokens, estimated_output_tokens):
        total = estimated_input_tokens + estimated_output_tokens
        return round(total / 1000 * self._cost_yuan_per_1k_tokens, 4)

    def get_capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities

    async def list_models(self) -> list[str]:
        return [self._default_model]

