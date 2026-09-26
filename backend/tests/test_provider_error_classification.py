"""Provider 错误分类回归测试。

这些错误会直接决定运行记录是可重试、需要充值，还是需要更换凭据；不能在
重试耗尽后退化成不具诊断价值的 ``unknown``。
"""

from __future__ import annotations

import pytest

from backend.app.agent.providers.base import ModelError, ModelErrorType


class _Response:
    def __init__(self, status_code: int, text: str = "error"):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {"error": self.text}


class _Client:
    def __init__(self, response: _Response):
        self.response = response

    async def post(self, *args, **kwargs):
        return self.response


class _SequenceClient:
    def __init__(self, responses: list[_Response]):
        self.responses = list(responses)
        self.calls = 0

    async def post(self, *args, **kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_cls", "kwargs"),
    [
        (
            "backend.app.agent.providers.deepseek_text.DeepSeekTextProvider",
            {"base_url": "https://example.invalid"},
        ),
        (
            "backend.app.agent.providers.openai_compat_text.OpenAICompatTextProvider",
            {"base_url": "https://example.invalid"},
        ),
    ],
)
async def test_balance_errors_are_classified(provider_cls, kwargs):
    module_name, class_name = provider_cls.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    provider = getattr(module, class_name)("sk-test", max_retries=1, **kwargs)
    provider._client = _Client(_Response(402, "balance"))
    with pytest.raises(ModelError) as exc:
        await provider._request_with_retry({})
    assert exc.value.error_type is ModelErrorType.INSUFFICIENT_BALANCE


@pytest.mark.asyncio
async def test_rate_limit_remains_classified_after_retries(monkeypatch):
    from backend.app.agent.providers.deepseek_text import DeepSeekTextProvider

    provider = DeepSeekTextProvider(
        "sk-test", base_url="https://example.invalid", max_retries=2,
    )
    provider._client = _Client(_Response(429, "slow down"))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("backend.app.agent.providers.deepseek_text.asyncio.sleep", no_sleep)
    with pytest.raises(ModelError) as exc:
        await provider._request_with_retry({})
    assert exc.value.error_type is ModelErrorType.RATE_LIMIT


@pytest.mark.asyncio
async def test_zero_retries_still_makes_one_request():
    from backend.app.agent.providers.deepseek_text import DeepSeekTextProvider

    provider = DeepSeekTextProvider(
        "sk-test", base_url="https://example.invalid", max_retries=0,
    )
    provider._client = _Client(_Response(402, "balance"))
    with pytest.raises(ModelError) as exc:
        await provider._request_with_retry({})
    assert exc.value.error_type is ModelErrorType.INSUFFICIENT_BALANCE


@pytest.mark.asyncio
async def test_one_retry_allows_second_openai_compatible_attempt(monkeypatch):
    from backend.app.agent.providers.openai_compat_text import OpenAICompatTextProvider

    provider = OpenAICompatTextProvider(
        "sk-test", base_url="https://example.invalid", max_retries=1,
    )
    client = _SequenceClient([
        _Response(500, "temporary"),
        _Response(200, "ok"),
    ])
    provider._client = client

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("backend.app.agent.providers.openai_compat_text.asyncio.sleep", no_sleep)
    result = await provider._request_with_retry({})

    assert result == {"error": "ok"}
    assert client.calls == 2
