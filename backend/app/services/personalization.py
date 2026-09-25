"""TeachMate 个性化提示构建。

个性化只影响称呼和表达风格，不得覆盖数据、隐私、安全和结构化输出约束。
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.orm import Session

from ..models import AppSetting


TONE_PROMPTS = {
    "rigorous": "采用严谨务实的语气：先给结论，再给数据依据和可执行步骤；避免空泛鼓励、夸张措辞和不必要的寒暄。",
    "friendly": "采用亲和友好的语气：表达自然、耐心、易懂；在保持事实准确的同时，给出适度鼓励和清晰的下一步建议。",
}


def read_personalization_settings(db: Session | None) -> dict[str, str]:
    """从 AppSetting 读取个性化字段，缺失时返回稳定默认值。"""
    values = {
        "user_address": "老师",
        "tone": "rigorous",
        "custom_prompt": "",
        "teacher_name": "",
        "subject": "英语",
    }
    if db is None:
        return values
    for key in values:
        item = db.get(AppSetting, key)
        if item is not None and item.value_json is not None:
            values[key] = str(item.value_json).strip()
    if values["tone"] not in {"rigorous", "friendly", "custom"}:
        values["tone"] = "rigorous"
    values["custom_prompt"] = values["custom_prompt"][:1200]
    values["user_address"] = values["user_address"][:50] or "老师"
    return values


def build_personalization_prompt(settings: Mapping[str, Any] | None) -> str:
    """将设置转换为可追加到系统提示的个性化段落。"""
    values = dict(settings or {})
    user_address = str(values.get("user_address") or "老师").strip()[:50]
    tone = str(values.get("tone") or "rigorous").strip().lower()
    custom_prompt = str(values.get("custom_prompt") or "").strip()[:1200]
    if tone not in {"rigorous", "friendly", "custom"}:
        tone = "rigorous"
    tone_prompt = TONE_PROMPTS.get(tone, "")
    if tone == "custom":
        tone_prompt = "遵循教师提供的自定义表达要求，但不得覆盖系统的事实、隐私、安全和结构化输出规则。"
    lines = [
        "个性化回答设置（仅影响称呼与表达方式，不改变数据权限、隐私规则、工具调用规则或安全约束）：",
        f"- 称呼用户为“{user_address}”；在回答中自然使用这一称呼，不要把它当作模型名称。",
        f"- {tone_prompt}",
    ]
    if tone == "custom" and custom_prompt:
        lines.append(f"- 教师自定义表达要求：{custom_prompt}")
    return "\n".join(lines)
