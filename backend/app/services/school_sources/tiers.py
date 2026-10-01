"""分层（A/B/C/D）取值归一与分层线推导。

这段逻辑原先只写在 MONI 适配器里，换学校就用不上。抽到共享位置后有两个好处：

- 内置 MONI 与自定义 MCP 数据源走**同一套**分层口径，导出的 A/B/C 线可比；
- 学校自己的叫法（甲/乙/丙、优秀/良好、Level 1..4）可以在数据源配置里覆盖别名。

设计口径（与 MONI 原有实现逐位一致）：

- 分层线取**该层实际出现的最低分**，吸附到 0.5 分粒度；
- 某一层没有学生时保持为空，**不用总分或其它科目推断**；
- 最后做 ``A ≥ B ≥ C`` 单调约束，符合 WorkBench 的线模型。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable

# 默认别名表：MONI 的 ELITE/KEY/GOOD/REGULAR 与常见中文叫法都归一到 A/B/C/D。
DEFAULT_TIER_ALIASES: dict[str, str] = {
    "ELITE": "A", "A": "A", "优": "A", "优秀": "A",
    "KEY": "B", "B": "B", "良": "B", "良好": "B",
    "GOOD": "C", "C": "C", "中": "C", "合格": "C",
    "REGULAR": "D", "D": "D", "普通": "D", "一般": "D",
}

TIER_LEVELS = ("A", "B", "C", "D")


def _normalize(value: Any) -> str:
    """查表键：去首尾空白、去空格、去「层」字、转大写。

    与 MONI 原有 ``_tier_key`` 的归一方式一致；``None`` 与空串都返回空键
    （注意不能直接 ``str(None)``，否则会得到字面量 ``"None"``）。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text.upper().replace(" ", "").replace("层", "")


@lru_cache(maxsize=32)
def _lookup_table(alias_items: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """默认别名表叠加配置覆盖后的查表（键已归一）。"""
    merged: dict[str, str] = {**DEFAULT_TIER_ALIASES, **dict(alias_items)}
    return {_normalize(name): str(level).strip().upper() for name, level in merged.items()}


def normalize_tier(value: Any, aliases: dict[str, str] | None = None) -> str | None:
    """把上游的分层叫法归一到 ``A/B/C/D``；认不出来时返回 ``None``。

    ``aliases`` 为数据源配置里的覆盖表，会**叠加**在默认别名表之上（同名覆盖）。
    刻意不做模糊匹配：认不出来就是 ``None``，让调用方显式处理，而不是猜一个层。
    """
    key = _normalize(value)
    if not key:
        return None
    items: tuple[tuple[str, str], ...] = tuple(sorted(aliases.items())) if aliases else ()
    level = _lookup_table(items).get(key)
    return level if level in TIER_LEVELS else None


def snap_half(value: float) -> float:
    """按 0.5 分粒度四舍五入，避免 Python round 的银行家舍入。"""
    return int(value * 2 + 0.5 + 1e-9) / 2


def derive_tier_cutoffs(
    pairs: Iterable[tuple[str | None, float | None]], *, full_score: float
) -> dict[str, float | None]:
    """由 ``(分层, 分数)`` 序列推导 A/B/C 三条分层线。

    只使用已经归一成功的分层；分数为 ``None`` 的记录直接跳过，不当作 0 分参与取最小。
    """
    by_tier: dict[str, list[float]] = {key: [] for key in TIER_LEVELS}
    for tier, score in pairs:
        if tier in by_tier and score is not None:
            by_tier[tier].append(float(score))
    raw: dict[str, float | None] = {
        key: (snap_half(min(by_tier[key])) if by_tier[key] else None) for key in ("A", "B", "C")
    }
    # 仅对已知层做上限和单调约束；未知层仍为空。
    for key in raw:
        if raw[key] is not None:
            raw[key] = min(max(raw[key], 0.0), full_score)
    if raw["B"] is not None and raw["A"] is not None:
        raw["A"] = max(raw["A"], raw["B"])
    if raw["C"] is not None and raw["B"] is not None:
        raw["B"] = max(raw["B"], raw["C"])
    if raw["B"] is not None and raw["A"] is not None:
        raw["A"] = max(raw["A"], raw["B"])
    return {"tier_a_cutoff": raw["A"], "tier_b_cutoff": raw["B"], "tier_c_cutoff": raw["C"]}
