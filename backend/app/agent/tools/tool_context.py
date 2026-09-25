"""工具运行时上下文

为工具处理函数提供数据库会话和作用域信息。
工具注册表在每次 Agent 循环开始时注入 ToolContext，
工具处理函数通过它访问数据库。

设计：使用 contextvars 避免在每个工具函数签名中传递 db session。
"""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..evidence import EvidenceLedger

# 全局 contextvar，在 Agent 循环开始时设置
_tool_context: contextvars.ContextVar["ToolContext | None"] = contextvars.ContextVar(
    "_tool_context", default=None
)


@dataclass
class ToolContext:
    """工具运行时上下文。"""
    db_session: Any = None  # SQLAlchemy Session
    scope: dict[str, Any] = field(default_factory=dict)
    # 如 {"exam_id": 123, "class_id": 5, "student_id": 42}
    cancel_event: threading.Event = field(default_factory=threading.Event)
    evidence_ledger: "EvidenceLedger | None" = None

    @property
    def exam_id(self) -> int | None:
        return self.scope.get("exam_id")

    @property
    def class_id(self) -> int | None:
        return self.scope.get("class_id")

    @property
    def student_id(self) -> int | None:
        return self.scope.get("student_id")

    @property
    def term_id(self) -> int | None:
        return self.scope.get("term_id")

    @property
    def run_id(self) -> int | None:
        return self.scope.get("run_id")

    @property
    def cancelled(self) -> bool:
        """工具是否已被请求取消。"""
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        """如果已取消则抛出 TimeoutError，供工具处理函数在循环中调用。"""
        if self.cancel_event.is_set():
            raise TimeoutError("工具执行已被取消")


def get_tool_context() -> ToolContext | None:
    """获取当前工具上下文。"""
    return _tool_context.get()


def set_tool_context(ctx: ToolContext | None) -> contextvars.Token:
    """设置当前工具上下文，返回 token 用于恢复。"""
    return _tool_context.set(ctx)


def reset_tool_context(token: contextvars.Token) -> None:
    """恢复工具上下文。"""
    _tool_context.reset(token)
