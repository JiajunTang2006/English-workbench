"""Agent Runtime 抽象层

提供统一的 AgentRuntime 接口，支持 legacy 和 harness 两种实现。

架构决策（2026-08）：
    生产路径统一使用 Node Harness（teachmate_runtime.py → examples/teachmate/cordis.yml）。
    Python SDK 版 HarnessRuntime（harness.py）仅保留为实验性探索，不作为生产入口。
    AGENT_RUNTIME=harness 环境变量仅用于隔离测试，不影响实际 launcher 行为。

使用方式::

    from backend.app.agent.runtime import get_runtime, AgentRuntime

    runtime = get_runtime()
    result = await runtime.run(request)

默认运行时为 legacy。AGENT_RUNTIME=harness 可切换到 Python SDK 实验路径
（需 deepseek-harness-sdk 已安装，否则 fail closed 回退到 legacy）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import (
    AgentRuntime,
    RunRequest,
    RunResult,
    RuntimeEvent,
    CancellationToken,
    EventSink,
)
from .legacy import LegacyRuntime
from .harness import HarnessRuntime
from .harness_event_mapper import map_harness_event, map_harness_events
from .harness_adapter import HarnessRunAdapter, HarnessRunResult

logger = logging.getLogger(__name__)

# 单例缓存
_runtime_instance: AgentRuntime | None = None


def get_runtime() -> AgentRuntime:
    """获取当前运行时单例。

    根据 AGENT_RUNTIME 环境变量选择运行时：
    - "legacy"（默认）：使用 LegacyRuntime
    - "harness"：实验性 Python SDK 路径（需 deepseek-harness-sdk 已安装）
      注意：生产入口使用 Node Harness（teachmate_runtime.py），
      此选项仅用于隔离测试和探索，不影响 launcher 行为。
    - 未知值：fail closed 到 legacy，记录警告
    """
    global _runtime_instance

    if _runtime_instance is not None:
        return _runtime_instance

    runtime_kind = os.getenv("AGENT_RUNTIME", "legacy").strip().lower()

    if runtime_kind == "harness":
        # 尝试创建 HarnessRuntime
        try:
            from ..config import get_agent_config
            config = get_agent_config()
            _runtime_instance = HarnessRuntime(config)
            logger.info("AGENT_RUNTIME=harness, HarnessRuntime created")
            return _runtime_instance
        except Exception as e:
            logger.warning(
                "AGENT_RUNTIME=harness 但 HarnessRuntime 创建失败 (%s)，回退到 legacy", e
            )
            runtime_kind = "legacy"

    if runtime_kind != "legacy":
        logger.warning(
            "未知 AGENT_RUNTIME 值 '%s'，fail closed 到 legacy", runtime_kind
        )
        runtime_kind = "legacy"

    # 创建 LegacyRuntime
    from ..factory import get_orchestrator
    from ..config import get_agent_config

    config = get_agent_config()
    orchestrator = get_orchestrator()

    if orchestrator is None:
        raise RuntimeError(
            "AgentOrchestrator 尚未创建，请先调用 factory.get_or_create_orchestrator()"
        )

    _runtime_instance = LegacyRuntime(orchestrator, config)
    return _runtime_instance


def set_runtime(runtime: AgentRuntime) -> None:
    """设置运行时单例（用于测试或显式初始化）。"""
    global _runtime_instance
    _runtime_instance = runtime


def clear_runtime() -> None:
    """清除运行时单例。"""
    global _runtime_instance
    _runtime_instance = None


__all__ = [
    "AgentRuntime",
    "LegacyRuntime",
    "HarnessRuntime",
    "HarnessRunAdapter",
    "HarnessRunResult",
    "map_harness_event",
    "map_harness_events",
    "RunRequest",
    "RunResult",
    "RuntimeEvent",
    "CancellationToken",
    "EventSink",
    "get_runtime",
    "set_runtime",
    "clear_runtime",
]
