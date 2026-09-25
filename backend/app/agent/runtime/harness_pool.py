"""受控 Harness 运行池。

每个槽位拥有独立 HarnessManager、会话根目录和 Education Bridge scope 根目录，
从而避免多个独立 Agent 共用一条 JSON-RPC 工作线程或互相覆盖 scope。默认
池大小为 4；仍可通过 ``TEACHMATE_HARNESS_POOL_SIZE`` 调低并发以适配低配设备。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .harness_manager import HarnessConfig, HarnessManager

logger = logging.getLogger(__name__)


def resolve_pool_size(value: int | str | None = None) -> int:
    raw = value if value is not None else os.getenv("TEACHMATE_HARNESS_POOL_SIZE", "4")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, 4))


@dataclass(frozen=True)
class PoolHealth:
    configured: bool
    running: bool
    queue_depth: int
    active_slots: int
    pool_size: int
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """兼容 HarnessManager.health() 的可序列化状态接口。"""
        return asdict(self)


class HarnessPool:
    """按租约分配 Harness 槽位的异步运行池。"""

    def __init__(self, config: HarnessConfig, *, size: int | str | None = None):
        self._base_config = config
        self._size = resolve_pool_size(size)
        self._managers: list[HarnessManager] = []
        self._available: asyncio.Queue[int] = asyncio.Queue()
        self._leased: dict[str, int] = {}
        self._lease_lock = asyncio.Lock()
        self._started = False
        self._last_error: str | None = None
        self._callbacks: list[Callable[[str, dict[str, Any]], Any]] = []
        self._build_managers()

    @property
    def pool_size(self) -> int:
        return self._size

    @property
    def is_running(self) -> bool:
        return self._started and all(manager.is_running for manager in self._managers)

    @property
    def managers(self) -> tuple[HarnessManager, ...]:
        return tuple(self._managers)

    def _build_managers(self) -> None:
        base_session = Path(self._base_config.session_root)
        base_scope = Path(self._base_config.scope_root) if self._base_config.scope_root else None
        for index in range(self._size):
            suffix = f"pool-{index + 1}"
            config = replace(
                self._base_config,
                session_root=str(base_session / suffix),
                scope_root=str(base_scope / suffix) if base_scope is not None else "",
            )
            self._managers.append(HarnessManager(config))

    async def start(self) -> None:
        if self._started:
            return
        started: list[HarnessManager] = []
        try:
            # 槽位拥有独立的 Node 进程、会话目录和 scope 根目录，启动阶段
            # 没有共享资源竞争。并行启动可把首次打开工作台的等待时间从
            # “四个进程依次初始化”降为“最慢的一个进程初始化”。
            results = await asyncio.gather(
                *(manager.start() for manager in self._managers),
                return_exceptions=True,
            )
            failures: list[BaseException] = []
            for manager, result in zip(self._managers, results):
                if isinstance(result, BaseException):
                    failures.append(result)
                else:
                    started.append(manager)
            if failures:
                first = failures[0]
                raise RuntimeError(
                    f"Harness 槽位启动失败（{len(failures)}/{len(self._managers)}）：{first}"
                ) from first
            self._started = True
            for index in range(self._size):
                self._available.put_nowait(index)
        except Exception as exc:
            self._last_error = str(exc)
            for manager in started:
                try:
                    await manager.shutdown()
                except Exception:
                    logger.warning("关闭已启动 Harness 槽位失败", exc_info=True)
            raise

    async def shutdown(self) -> None:
        if not self._managers:
            return
        self._started = False
        for manager in self._managers:
            try:
                await manager.shutdown()
            except Exception:
                logger.warning("关闭 Harness 槽位失败", exc_info=True)
        while not self._available.empty():
            try:
                self._available.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._leased.clear()

    async def acquire(self, lease_key: str | int, *, timeout: float | None = None) -> HarnessManager:
        if not self._started:
            raise RuntimeError("HarnessPool not running")
        key = str(lease_key)
        async with self._lease_lock:
            if key in self._leased:
                return self._managers[self._leased[key]]
        try:
            if timeout is None:
                index = await self._available.get()
            else:
                index = await asyncio.wait_for(self._available.get(), timeout=max(0.1, timeout))
        except asyncio.TimeoutError as exc:
            raise TimeoutError("Harness 运行槽位暂时不可用") from exc
        async with self._lease_lock:
            # 两个协程可能同时为同一个 run 请求租约；只保留第一个租约，
            # 将后来取出的槽位放回队列，避免槽位泄漏导致池逐渐“少一个”。
            existing = self._leased.get(key)
            if existing is not None:
                self._available.put_nowait(index)
                return self._managers[existing]
            self._leased[key] = index
        return self._managers[index]

    async def release(self, lease_key: str | int) -> None:
        key = str(lease_key)
        async with self._lease_lock:
            index = self._leased.pop(key, None)
        if index is not None and self._started:
            self._available.put_nowait(index)

    def health(self) -> PoolHealth:
        configured = bool(self._managers) and all(manager.health().configured for manager in self._managers)
        active = len(self._leased)
        return PoolHealth(
            configured=configured,
            running=self.is_running,
            queue_depth=self._available.qsize(),
            active_slots=active,
            pool_size=self._size,
            last_error=self._last_error,
        )

    def register_event_callback(self, callback: Callable[[str, dict[str, Any]], Any]) -> None:
        self._callbacks.append(callback)
        for manager in self._managers:
            manager.register_event_callback(callback)

    async def cancel_session(self, harness_session_id: str) -> bool:
        cancelled = False
        for manager in self._managers:
            try:
                cancelled = await manager.cancel_session(harness_session_id) or cancelled
            except Exception:
                logger.warning("取消 Harness 会话失败", exc_info=True)
        return cancelled

    async def apply_config(self, config: HarnessConfig, *, restart_if_running: bool = True) -> bool:
        """将配置一致地应用到所有槽位。"""
        restarted = False
        for manager in self._managers:
            # 保留槽位隔离目录；只替换模型/API 等共享配置。
            target = replace(
                config,
                session_root=manager._config.session_root if manager._config else config.session_root,
                scope_root=manager._config.scope_root if manager._config else config.scope_root,
            )
            restarted = await manager.apply_config(target, restart_if_running=restart_if_running) or restarted
        self._base_config = config
        return restarted
