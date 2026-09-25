"""模型输入/输出 Token 预算。

长附件属于输入上下文，不应反向放大模型的思考或输出额度。这里把两类预算
分开计算，并使用偏保守的中英文混合文本估算，避免中文 PDF 按 ``字符/4``
严重低估。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


_CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\u3040-\u30ff\uac00-\ud7af]"
)


@dataclass(frozen=True)
class TokenBudgetPlan:
    mode: str
    context_limit: int
    output_limit: int
    prompt_limit: int
    formal_context_limit: int
    history_limit: int


def estimate_text_tokens(text: str | None) -> int:
    """保守估算中英文混合文本 token 数，不依赖特定厂商 tokenizer。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    non_cjk = max(0, len(text) - cjk)
    # 中文通常接近 1 字/token；英文、数字和标点按约 4 字符/token。
    # 再留少量消息格式开销，宁可轻微高估也不让长 PDF 溢出预算。
    return max(1, cjk + math.ceil(non_cjk / 4) + 8)


def truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    """把文本截到估算 token 上限；保留前缀并明确标注裁剪。"""
    if max_tokens <= 0 or not text:
        return ""
    if estimate_text_tokens(text) <= max_tokens:
        return text
    marker = "\n\n[内容已按本次输入 Token 预算裁剪]"
    marker_tokens = estimate_text_tokens(marker)
    target = max(1, max_tokens - marker_tokens)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_text_tokens(text[:mid]) <= target:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + marker


def resolve_token_budget(config: Any) -> TokenBudgetPlan:
    """按思考模式和模型能力计算输入/输出预算。

    快速模式优先首字延迟；深度模式放宽推理输出和正式附件上下文。任何档位都
    不超过模型档案声明的 context/max_output 上限。
    """
    context_limit = max(2_048, int(getattr(config, "text_context_length", 128_000) or 128_000))
    configured_output = max(
        256, int(getattr(config, "text_max_output_tokens", 4_096) or 4_096)
    )
    thinking = bool(getattr(config, "text_thinking_enabled", True))
    effort = str(getattr(config, "text_reasoning_effort", "high") or "high").lower()

    if not thinking or effort in {"off", "disabled"}:
        mode = "quick"
        output_target = 2_048
        prompt_target = 12_000
        formal_target = 6_000
        history_target = 2_000
    elif effort == "max":
        mode = "deep_max"
        output_target = 8_192
        prompt_target = 48_000
        formal_target = 32_000
        history_target = 4_000
    else:
        mode = "deep"
        output_target = 4_096
        prompt_target = 32_000
        formal_target = 20_000
        history_target = 3_000

    output_limit = min(configured_output, output_target)
    # 为系统提示、工具 schema、消息包装和 Provider 误差预留空间。
    usable_input = max(2_048, context_limit - output_limit - 2_048)
    prompt_limit = min(prompt_target, usable_input)
    formal_context_limit = min(formal_target, max(1_024, prompt_limit * 2 // 3))
    history_limit = min(history_target, max(512, prompt_limit - formal_context_limit - 2_000))
    return TokenBudgetPlan(
        mode=mode,
        context_limit=context_limit,
        output_limit=output_limit,
        prompt_limit=prompt_limit,
        formal_context_limit=formal_context_limit,
        history_limit=history_limit,
    )
