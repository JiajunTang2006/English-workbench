"""Agent Runtime 抽象层

定义统一的 AgentRuntime 接口，支持 legacy 和 harness 两种实现。
legacy 包装现有 AgentOrchestrator，不改变行为；
harness 通过 deepseek-harness-sdk 驱动 Harness（H0 阶段实现）。

文档 §18.4 定义的数据流::

    TeachMate Frontend
            │ 现有 API / SSE 契约
            ▼
    FastAPI Agent Router / Run Executor
            │
            ▼
    AgentRuntime Interface
       ├── LegacyRuntime  ──> 当前 AgentOrchestrator
       └── HarnessRuntime ──> deepseek-harness-sdk
                                    │
                                    ▼
                             TeachMate Tool Bridge
                                    │
                                    ▼
              Scope Guard → Python 教育工具 → Evidence → Redaction
                                    │
                                    ▼
                           SQLite 事实源
"""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

if TYPE_CHECKING:
    from ..orchestrator import OrchestratorRequest, OrchestratorResponse
    from ..config import AgentConfig


# ---------------------------------------------------------------------------
# 事件回调
# ---------------------------------------------------------------------------

@dataclass
class RuntimeEvent:
    """运行时产生的标准化事件。

    事件类型由 event_types.py 中的常量约束。
    """
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0


EventSink = Callable[[RuntimeEvent], None]
"""事件回调类型。接收 RuntimeEvent，无返回值。"""


# ---------------------------------------------------------------------------
# 取消令牌
# ---------------------------------------------------------------------------

class CancellationToken:
    """协作式取消令牌。

    可跨线程使用（内部使用 threading.Event）。
    工具执行时检查此令牌，实现可终止的超时机制。
    """

    def __init__(self) -> None:
        import threading
        self._event = threading.Event()

    def cancel(self) -> None:
        """请求取消。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """是否已请求取消。"""
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """等待取消信号。"""
        return self._event.wait(timeout)


# ---------------------------------------------------------------------------
# 运行请求
# ---------------------------------------------------------------------------

@dataclass
class RunRequest:
    """运行时统一的运行请求。

    封装 OrchestratorRequest，增加 event_sink 和 cancellation_token。
    """
    orchestrator_request: "OrchestratorRequest"
    event_sink: EventSink | None = None
    cancellation_token: CancellationToken | None = None
    db_session: Any = None  # 数据库会话，注入到工具上下文


# ---------------------------------------------------------------------------
# 运行结果
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """运行时统一的运行结果。

    封装 OrchestratorResponse，增加 runtime 元信息。
    """
    success: bool
    answer: str
    structured_answer: dict | None = None
    evidence: list[dict] | None = None
    cost_yuan: float | None = 0.0
    tokens_used: int = 0
    elapsed_ms: int = 0
    stop_reason: str = ""
    error: str | None = None
    needs_confirmation: bool = False
    validation: Any = None  # ValidationResult
    request_ids: list[str] = field(default_factory=list)
    steps: list[Any] = field(default_factory=list)  # LoopStep 列表
    runtime_kind: str = "legacy"
    runtime_version: str = ""


# ---------------------------------------------------------------------------
# AgentRuntime 接口
# ---------------------------------------------------------------------------

class AgentRuntime(abc.ABC):
    """Agent 运行时抽象接口。

    所有运行时（legacy、harness）必须实现此接口。
    路由层和执行器通过此接口调用运行时，不直接依赖具体实现。

    生命周期：
    1. 创建：通过工厂函数创建，传入 config
    2. 运行：调用 run()，可多次调用
    3. 关闭：调用 close()，释放资源
    """

    @abc.abstractmethod
    async def run(self, request: RunRequest) -> RunResult:
        """执行一次 Agent 运行。

        :param request: 运行请求，包含编排器请求、事件回调和取消令牌
        :return: 运行结果
        """
        ...

    @abc.abstractmethod
    async def cancel(self, run_id: str) -> bool:
        """取消正在运行的运行。

        :param run_id: 运行 ID
        :return: 是否成功取消（已终态时返回 False）
        """
        ...

    @abc.abstractmethod
    async def confirm(self, run_id: str, confirmation: dict) -> RunResult:
        """确认运行（如预算确认）。

        :param run_id: 运行 ID
        :param confirmation: 确认信息（如确认的预算金额）
        :return: 确认后的运行结果
        """
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        """关闭运行时，释放资源（如 provider 连接、线程池）。"""
        ...

    @abc.abstractmethod
    def health(self) -> dict[str, Any]:
        """健康检查。

        :return: 包含 status、runtime_kind、runtime_version 等信息的字典
        """
        ...

    @abc.abstractmethod
    def runtime_version(self) -> str:
        """返回运行时版本标识。"""
        ...
