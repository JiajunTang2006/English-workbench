"""本地 Provider 模拟器的错误矩阵和请求记录测试。"""

import pytest

from backend.app.agent.providers.base import (
    ModelError,
    ModelErrorType,
    ModelResponse,
)
from backend.app.agent.providers.fake_text import FakeProviderStep, FakeTextProvider


@pytest.mark.asyncio
async def test_fake_provider_records_payload_and_consumes_steps() -> None:
    provider = FakeTextProvider([
        ModelResponse(content='{"ok":true}'),
        ModelResponse(content="第二轮"),
    ])

    first = await provider.complete(
        [{"role": "user", "content": "student_01"}],
        response_format={"type": "json_object"},
    )
    second = await provider.complete(
        [{"role": "user", "content": "继续"}],
    )

    assert first.content == '{"ok":true}'
    assert second.content == "第二轮"
    assert len(provider.calls) == 2
    assert provider.calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        ModelErrorType.AUTH_FAILED,
        ModelErrorType.RATE_LIMIT,
        ModelErrorType.TIMEOUT,
        ModelErrorType.NETWORK,
        ModelErrorType.INVALID_RESPONSE,
    ],
)
async def test_fake_provider_replays_classified_errors(error_type: ModelErrorType) -> None:
    provider = FakeTextProvider([
        FakeProviderStep(error_type=error_type, error_message="测试错误")
    ])

    with pytest.raises(ModelError) as exc_info:
        await provider.complete([])

    assert exc_info.value.error_type is error_type
    assert len(provider.calls) == 1

