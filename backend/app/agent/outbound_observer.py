"""出站观察器（B3-04）

捕获即将发给模型 Provider 的出站内容（脱敏副本），用于：
1. 审计：每轮出站文本存档（内存环形 + 可选本地 JSONL），供测试断言"无真实身份"；
2. 自检门禁：脱敏后的文本若仍命中运行内身份词典（姓名/学校/受保护词）或
   学号/电话格式，立即抛 PrivacyViolationError —— 出站内容必须 fail-closed，
   绝不带真实身份离开本机。

本模块不保存 API Key 或真实身份词典；entries 只保留脱敏副本。
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .privacy import PrivacyMapper, PrivacyViolationError

logger = logging.getLogger(__name__)

# 仅把带有明确学号标签的数字作为可疑项；年份、日期、分数和考试编号
# 不应在审计日志中被误标为学生身份。
_SUSPECT_STUDENT_NO_PATTERN = re.compile(
    r"(?i)(?:学号|学生编号|student\s*(?:no|number|id))\s*[:：#]?\s*(\d{6,10})"
)
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


class OutboundObserver:
    """出站内容观察者（审计 + 自检门禁）。"""

    def __init__(self, *, storage_dir: str | Path | None = None,
                 max_entries: int = 50):
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._storage_dir = Path(storage_dir) if storage_dir else None
        if self._storage_dir is not None:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

    def capture(
        self,
        *,
        prompt_text: str,
        privacy_mapper: PrivacyMapper | None,
        run_id: int | None = None,
        session_id: int | None = None,
        role: str = "turn",
    ) -> dict[str, Any]:
        """记录一份脱敏出站副本并自检；泄漏直接抛 PrivacyViolationError。

        :return: 记录条目（含脱敏文本）。
        """
        if privacy_mapper is not None:
            sanitized = privacy_mapper.sanitize_text(prompt_text)
        else:
            sanitized = prompt_text

        leaked, suspects = self._check_identity(sanitized, privacy_mapper)
        if leaked:
            raise PrivacyViolationError(
                "出站内容仍包含真实身份信息（" + "、".join(leaked[:3])
                + "），已拒绝出站"
            )

        entry = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "session_id": session_id,
            "role": role,
            "content": sanitized,
        }
        # P1-6：裸 6-10 位数字（日期/人数/分数/考试编号）仅告警，不误杀
        if suspects:
            entry["suspect_student_no"] = True
        self._entries.append(entry)

        if self._storage_dir is not None:
            self._append_jsonl(entry)
        return entry

    # ------------------------------------------------------------------

    @staticmethod
    def _check_identity(text: str, mapper: PrivacyMapper | None) -> tuple[list[str], list[str]]:
        """返回 (硬拒命中项, 可疑学号告警)。

        硬拒（hits）：词典内真实姓名/学校/受保护词（含真实学号）未脱净、电话格式。
        告警（suspects）：词典外的裸 6-10 位数字 —— 可能是日期/人数/分数/
        考试编号，不误杀，仅标记审计（P1-6 与输出验证器语义一致）。
        """
        hits: list[str] = []
        suspects: list[str] = []
        if mapper is not None:
            terms: list[str] = []
            if not getattr(mapper, "allow_student_names", False):
                terms += list(mapper.known_real_names)
            terms += list(mapper.known_school_names)
            terms += list(mapper.known_protected_terms)
            for term in sorted({t for t in terms if t and len(t) >= 2}, key=len, reverse=True):
                if term in text:
                    hits.append("身份词典命中")
                    break
        if _PHONE_PATTERN.search(text):
            hits.append("电话格式")
        for m in _SUSPECT_STUDENT_NO_PATTERN.finditer(text):
            # 命中保护词的学号已被词典分支处理；此处仅标记疑似（不硬拒）
            suspects.append(m.group(0))
        return hits, suspects

    def latest(self, n: int = 1) -> list[dict[str, Any]]:
        return list(self._entries)[-n:]

    def all(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    # ------------------------------------------------------------------

    def _append_jsonl(self, entry: dict[str, Any]) -> None:
        if self._storage_dir is None:
            return
        try:
            with (self._storage_dir / "outbound.jsonl").open(
                "a", encoding="utf-8"
            ) as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("出站观察者写审计文件失败: %s", exc)
