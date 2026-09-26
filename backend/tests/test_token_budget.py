from types import SimpleNamespace

from backend.app.agent.token_budget import (
    estimate_text_tokens,
    resolve_token_budget,
    truncate_text_to_tokens,
)


def _cfg(**overrides):
    base = {
        "text_context_length": 128_000,
        "text_max_output_tokens": 8_192,
        "text_thinking_enabled": False,
        "text_reasoning_effort": "off",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_quick_and_deep_budgets_are_separate():
    quick = resolve_token_budget(_cfg())
    deep = resolve_token_budget(_cfg(
        text_thinking_enabled=True, text_reasoning_effort="high",
    ))
    maximum = resolve_token_budget(_cfg(
        text_thinking_enabled=True, text_reasoning_effort="max",
    ))

    assert quick.mode == "quick"
    assert quick.output_limit == 2_048
    assert quick.formal_context_limit == 6_000
    assert quick.history_limit == 2_000
    assert deep.output_limit == 4_096
    assert deep.formal_context_limit == 20_000
    assert deep.history_limit == 3_000
    assert maximum.output_limit == 8_192
    assert maximum.formal_context_limit == 32_000
    assert maximum.history_limit == 4_000


def test_profile_output_cap_always_wins():
    plan = resolve_token_budget(_cfg(
        text_thinking_enabled=True,
        text_reasoning_effort="max",
        text_max_output_tokens=3_000,
    ))
    assert plan.output_limit == 3_000


def test_chinese_pdf_text_is_not_estimated_as_ascii_quarter():
    chinese = "成绩分析" * 1_000
    ascii_text = "analysis" * 1_000
    assert estimate_text_tokens(chinese) >= 4_000
    assert estimate_text_tokens(ascii_text) < len(ascii_text) // 2


def test_token_truncation_respects_budget():
    text = ("本页是学生成绩统计。" * 2_000) + ("score data " * 2_000)
    clipped = truncate_text_to_tokens(text, 1_000)
    assert "输入 Token 预算裁剪" in clipped
    assert estimate_text_tokens(clipped) <= 1_000


def test_harness_manager_forwards_output_limit_to_sdk(monkeypatch, tmp_path):
    import deepseek_harness.api as sdk_api
    from backend.app.agent.runtime.harness_manager import HarnessConfig, HarnessManager

    captured = {}

    class FakeHarness:
        def __init__(self, config):
            captured["config"] = config

        def start(self):
            captured["started"] = True

        def close(self):
            pass

    monkeypatch.setattr(
        sdk_api, "DeepSeekHarnessConfig",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(sdk_api, "DeepSeekHarness", FakeHarness)
    manager = HarnessManager(HarnessConfig(
        cordis_path=str(tmp_path / "cordis.yml"),
        session_root=str(tmp_path / "sessions"),
        api_key="sk-test",
        runtime_bin="runtime.js",
        max_tokens=2_048,
    ))
    manager._start_process()
    assert captured["started"] is True
    assert captured["config"].max_tokens == 2_048
