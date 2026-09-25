"""后台任务处理器包 (U4-01)

注册所有内置任务处理器到 Worker。
"""

from __future__ import annotations

from .base import register_all_handlers

__all__ = ["register_all_handlers"]
