"""
HarnessManager — 常驻 JSON-RPC 运行时管理器 (U1-02 / B2-01)

职责：
1. FastAPI lifespan 懒启动 DeepSeekHarness 实例（vendored SDK 客户端）
2. 统一传入模型 / API Base / API Key / Session Root / Cordis 配置
3. 专用工作线程串行调用同步 SDK，避免阻塞 asyncio 主循环
4. session.event 等通知安全转交 asyncio 主循环（事件投影）
5. 检测子进程退出并有上限自动重启
6. shutdown 等待当前任务收尾，in-flight Future 收到明确异常
7. 健康检查只返回已配置/未配置和运行状态，不返回 Key

真实 JSON-RPC 协议（由 vendored SDK 客户端实现，本类不自行实现协议）：
- initialize / session/prompt / shutdown
- session/prompt 只返回 messageId；最终答案来自 notification 流
  （agent/inbox/spliced 回执 → session.event → session.status=idle），
  SDK Session.run 已封装；上层通过 manager.call("session/prompt", ...) 拿到
  {sessionId, finalResponse, finishReason, events} 完整回合结果。
- 不存在 session/create、session/send、session/close、session/cancel 方法；
  会话通过 sessionId 复用，取消用 session/prompt 回合的本地取消标记实现。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from queue import Queue, Empty

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_MAX_RESTARTS = 10                 # B5-01：从 3 加宽到 10，避免历史崩溃计数"锁死"常驻进程
_RESTART_COOLDOWN_SEC = 2.0
# B5-01：重启计数时间窗口 —— 距离上次崩溃超过该秒数自动清零预算
# （避免一次性历史包袱让用户重启 mac 之外无任何办法恢复）。
_RESTART_BUDGET_RESET_SEC = 300.0
_WORKER_JOIN_TIMEOUT_SEC = 10.0
_DEFAULT_CALL_TIMEOUT_SEC = 120.0

# 真实协议支持的方法（其余方法名一律明确失败，静默不出现）
SUPPORTED_RPC_METHODS = frozenset({"initialize", "session/prompt", "shutdown"})


class _SessionCancelled(Exception):
    """内部异常：会话回合被本地取消标记提前终止。"""


def _notify_wrapper(fn: Callable[[Any], None]) -> Callable[[Any], None]:
    """包装 SDK notification 回调：忽略 SDK 订阅关闭后的自终止异常。

    Session.run 内部在退出时可能向回调传最终 notification；wrapper
    保证取消/关闭时回调异常不会吞掉主流程错误。
    """

    def wrapper(notification: Any) -> None:
        fn(notification)

    return wrapper


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class HarnessConfig:
    """启动 Harness 所需的全部配置。"""
    cordis_path: str
    session_root: str
    model: str = "deepseek-chat"
    # initialize.maxTokens：限制单回合总输出（含思考文本），防止快速模式失控。
    max_tokens: int = 2_048
    api_base: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    node_path: str = "node"
    # vendored JSON-RPC 运行时入口（dsh-sdk-jsonrpc-demo/lib/bin.js）
    runtime_bin: str = ""
    # B3-01：官方运行时 argv（exe 单文件 / dev node 闭包），与 runtime_bin 二选一
    launch_args_override: Optional[tuple[str, ...]] = None
    extra_env: Dict[str, str] = field(default_factory=dict)
    # B3-03：Education Bridge scope 注入目录（服务器回合前写入 per-session scope）
    scope_root: str = ""
    # B3-10：配置版本号（随 agent_cfg.config_version 绑定，run 审计用）
    config_version: int = 1

    @property
    def is_configured(self) -> bool:
        # 官方产物（launch_args_override）或 vendored 闭包（runtime_bin）任一可用即可
        return bool(
            self.api_key and self.cordis_path and self.session_root
            and (self.runtime_bin or self.launch_args_override)
        )


@dataclass
class HealthStatus:
    """健康检查结果（不含敏感信息）。"""
    configured: bool
    running: bool
    pid: Optional[int] = None
    restart_count: int = 0
    last_error: Optional[str] = None
    queue_depth: int = 0
    active_method: Optional[str] = None
    generation: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "running": self.running,
            "pid": self.pid,
            "restart_count": self.restart_count,
            "last_error": self.last_error,
            "queue_depth": self.queue_depth,
            "active_method": self.active_method,
            "generation": self.generation,
        }


@dataclass
class _TaskRequest:
    """工作线程上的一个调用请求。"""
    method: str
    params: Dict[str, Any]
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop
    timeout: float = _DEFAULT_CALL_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# HarnessManager
# ---------------------------------------------------------------------------

class HarnessManager:
    """
    管理常驻 DeepSeekHarness JSON-RPC 子进程。

    线程模型：
    - 主 asyncio 线程：接收调用请求，把 _TaskRequest 放入队列
    - 专用工作线程：从队列取请求，通过 stdin/stdout 与子进程通信，
      把结果/异常通过 loop.call_soon_threadsafe 回传到 asyncio Future
    """

    def __init__(self, config: Optional[HarnessConfig] = None):
        self._config: Optional[HarnessConfig] = config
        # vendored SDK 实例（内含 JSON-RPC 子进程与协议客户端）
        self._sdk = None  # type: ignore
        self._worker_thread: Optional[threading.Thread] = None
        self._task_queue: "Queue[Optional[_TaskRequest]]" = Queue()
        self._running: bool = False
        self._restart_count: int = 0
        self._last_restart_attempt_at: Optional[float] = None  # B5-01：上次重启尝试时间（用于时间窗口衰减）
        self._last_crash_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._event_callback: Optional[
            Callable[[str, Dict[str, Any]], Any]
        ] = None
        # 被取消的会话集合（真实协议无 session/cancel，用本地取消标记中断回合）
        self._cancelled_sessions: set[str] = set()
        self._cancel_lock = threading.Lock()
        # B2-01: 当前正在执行的请求（工作线程占用中），shutdown/崩溃时统一 settle
        self._active_request: Optional[_TaskRequest] = None
        self._active_lock = threading.Lock()
        self._generation: int = 0

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def configure(self, config: HarnessConfig) -> None:
        """更新配置。如果进程正在运行则先停止。

        B5-01：同时清零重启计数 + 上次崩溃时间，让"保存并应用"等主动操作
        成为解锁卡死的官方入口（无需重启整个应用）。
        """
        with self._lock:
            if self._running:
                self._stop_internal()
            self._config = config
            self._restart_count = 0
            self._last_restart_attempt_at = None
            self._last_crash_at = None
            self._last_error = None

    async def apply_config(self, config: HarnessConfig, *, restart_if_running: bool = True) -> bool:
        """B3-10：安全切换配置并（可选）重启常驻进程。

        - 进程未运行：仅更新配置（下次 start 生效）；
        - 进程运行中且 restart_if_running=True：停止 → 更新 → 启新进程；
        - 重启失败抛异常（不静默）。
        :return: 是否执行了重启
        """
        was_running = self.is_running
        self.configure(config)
        if was_running and restart_if_running:
            await self.start()
            return True
        return False

    async def start(self) -> None:
        """懒启动 Harness 子进程（SDK 实例 + initialize）。"""
        if self._running:
            return
        if not self._config or not self._config.is_configured:
            raise RuntimeError("HarnessManager not configured or config incomplete")

        self._loop = asyncio.get_running_loop()
        self._running = True
        self._restart_count = 0
        self._last_error = None

        # 启动工作线程
        self._worker_thread = threading.Thread(
            target=self._worker_loop, name="harness-worker", daemon=True
        )
        self._worker_thread.start()

        # 启动子进程（SDK 内部 Popen + initialize RPC）
        self._start_process()

        logger.info(
            "HarnessManager started, cordis=%s, model=%s",
            self._config.cordis_path,
            self._config.model,
        )

    async def shutdown(self) -> None:
        """优雅关闭：等待当前任务收尾，停止子进程和工作线程。"""
        if not self._running:
            return
        self._running = False

        # 排空队列中尚未处理的请求，设置明确异常（不用 CancelledError：
        # asyncio.wait_for 会把它当作任务取消重新抛出，导致上层误判为用户取消）
        drained: list[_TaskRequest] = []
        while True:
            try:
                item = self._task_queue.get_nowait()
            except Empty:
                break
            if item is not None:
                drained.append(item)
        for req in drained:
            self._resolve_future(
                req, None, RuntimeError("Harness shutdown while request queued"),
            )

        # B2-01: 对正在执行的请求也设置明确异常（若 work 线程仍在阻塞读取）
        active = self._active_request
        if active is not None and not active.future.done():
            self._resolve_future(
                active, None, RuntimeError("Harness shutdown while RPC in flight")
            )

        # 唤醒工作线程退出
        self._task_queue.put(None)

        # 等待工作线程
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=_WORKER_JOIN_TIMEOUT_SEC)

        self._stop_process()

        logger.info("HarnessManager shut down")

    async def call(
        self, method: str, *, timeout: float = _DEFAULT_CALL_TIMEOUT_SEC, **params: Any
    ) -> Any:
        """
        向 Harness 发送一个 JSON-RPC 调用并等待结果。

        :param timeout: 调用超时秒数，超时后 future 被取消。
        """
        if not self._running:
            raise RuntimeError("HarnessManager not running")
        if not self._loop:
            raise RuntimeError("No running event loop")

        future: asyncio.Future = self._loop.create_future()
        req = _TaskRequest(
            method=method,
            params=params,
            future=future,
            loop=self._loop,
            timeout=timeout,
        )
        self._task_queue.put(req)
        # 调用层负责给上游一个稳定、可诊断的超时；SDK/工作线程可能仍在
        # 收尾，因此同时把当前 Harness 会话标记为失效。上层会轮换业务会话
        # 的 harness_session_id，晚到 notification 只能命中旧的终态运行。
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            session_id = params.get("sessionId") or params.get("session_id")
            if method == "session/prompt" and session_id:
                await self.cancel_session(str(session_id))
            raise TimeoutError(
                f"Harness 回合超时（{timeout:g}s，session={session_id or 'unknown'}）"
            ) from exc

    async def cancel_session(self, harness_session_id: str) -> bool:
        """
        取消目标 Harness 会话的活跃回合（本地取消语义）。

        真实协议只有 initialize/session/prompt/shutdown，不存在
        session/cancel RPC：远端 turn 无法被强制中止。因此取消 =
        1) 本地标记该会话已取消；
        2) 正在等待该会话回合的工作线程在下次 notification 轮询时
           提前结束（future 收到明确异常）；
        3) 向事件投影广播 session.status=cancelled（SSE/轮询链路可见）。

        成功后该会话的后续 prompt 仍然可用（远端会自行走到 idle）。
        :returns: 是否已（重新）标记取消
        """
        if not self._running:
            logger.warning(
                "HarnessManager not running, cannot cancel session %s",
                harness_session_id,
            )
            return False

        with self._cancel_lock:
            self._cancelled_sessions.add(harness_session_id)
        self._dispatch_notification(
            "session.status",
            {"sessionId": harness_session_id, "status": "cancelled"},
        )
        logger.info("session/cancel 已注册（本地取消语义）: %s", harness_session_id)
        return True

    def _is_session_cancelled(self, session_id: str) -> bool:
        with self._cancel_lock:
            return session_id in self._cancelled_sessions

    def _clear_session_cancelled(self, session_id: str) -> None:
        """清理一次性取消标记。

        ``session/cancel`` 是本地协作式语义，不是远端永久状态。标记只应
        影响当前回合；如果不清理，教师取消一次后同一会话的后续提问会被
        永久拒绝。
        """
        with self._cancel_lock:
            self._cancelled_sessions.discard(session_id)

    def health(self) -> HealthStatus:
        """健康检查 — 不返回 API Key 等敏感信息。"""
        configured = bool(self._config and self._config.is_configured)
        pid = None
        sdk = self._sdk
        if sdk is not None:
            try:
                proc = sdk._client._proc  # SDK 内部子进程
                if proc is not None and proc.poll() is None:
                    pid = getattr(proc, "pid", None)
            except Exception:
                pid = None

        with self._active_lock:
            active_method = self._active_request.method if self._active_request else None
        return HealthStatus(
            configured=configured,
            running=self._running and pid is not None,
            pid=pid,
            restart_count=self._restart_count,
            last_error=self._redact_error(self._last_error),
            queue_depth=max(0, self._task_queue.qsize()),
            active_method=active_method,
            generation=self._generation,
        )

    def _redact_error(self, message: str | None) -> str | None:
        """健康接口中的错误只保留诊断信息，不回显 Key 或 Bearer token。"""
        if not message:
            return message
        redacted = str(message)
        cfg = self._config
        if cfg and cfg.api_key:
            redacted = redacted.replace(cfg.api_key, "[REDACTED]")
        redacted = re.sub(r"(?i)\b(?:sk|key|token)-[a-z0-9_-]{6,}", "[REDACTED]", redacted)
        redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", redacted)
        return redacted[:1000]

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # 内部：子进程管理
    # ------------------------------------------------------------------

    def _start_process(self) -> None:
        """启动 JSON-RPC 子进程（通过 vendored SDK，含 initialize 握手）。

        与真实协议对齐：
        - 启动命令为 ``node <runtime_bin>``（dsh-sdk-jsonrpc-demo/lib/bin.js）；
        - 环境变量使用 SDK 约定：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL /
          DSH_CORDIS_CONFIG / DSH_SESSION_ROOT / DSH_CWD / DSH_MODEL；
        - 启动成功后 SDK 自动完成 initialize 握手；失败抛出明确异常。
        """
        if not self._config:
            raise RuntimeError("No config")

        from deepseek_harness.api import DeepSeekHarness, DeepSeekHarnessConfig

        cfg = self._config
        cwd = os.getcwd()
        env = dict(cfg.extra_env)
        if cfg.scope_root:
            env["DSH_EDUCATION_SCOPE_ROOT"] = cfg.scope_root

        sdk_config = DeepSeekHarnessConfig(
            provider="deepseek-official",
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            cwd=cwd,
            session_root=cfg.session_root,
            cordis=cfg.cordis_path,
            env=env,
            runtime_bin=cfg.runtime_bin,
            launch_args_override=cfg.launch_args_override,
            base_url=cfg.api_base,
            api_key=cfg.api_key,
            request_timeout_seconds=_DEFAULT_CALL_TIMEOUT_SEC,
            shutdown_timeout_seconds=5.0,
        )
        harness = DeepSeekHarness(sdk_config)
        try:
            harness.start()  # Popen + initialize RPC
        except Exception:
            try:
                harness.close()
            except Exception:
                pass
            self._sdk = None
            raise

        self._sdk = harness

        logger.info("Harness SDK 子进程已启动（initialize 完成）")

    def _build_env(self) -> Dict[str, str]:
        """构建环境变量（供健康诊断/重启使用；实际由 SDK 构造）。"""
        cfg = self._config
        env = dict(os.environ)
        env["DEEPSEEK_API_KEY"] = cfg.api_key
        env["DEEPSEEK_BASE_URL"] = cfg.api_base
        env["DSH_CORDIS_CONFIG"] = cfg.cordis_path
        env["DSH_SESSION_ROOT"] = cfg.session_root
        env["DSH_MODEL"] = cfg.model
        env.update(cfg.extra_env)
        if cfg.scope_root:
            env["DSH_EDUCATION_SCOPE_ROOT"] = cfg.scope_root
        return env

    def _stop_process(self) -> None:
        """停止子进程：SDK 发送 shutdown RPC 后 terminate 并回收。"""
        sdk = self._sdk
        self._sdk = None
        if sdk is None:
            return
        try:
            sdk.close()
        except Exception as e:
            logger.warning("Error stopping Harness SDK runtime: %s", e)
        finally:
            self._generation += 1

    def _stop_internal(self) -> None:
        """内部停止（持锁调用）。"""
        self._running = False
        self._task_queue.put(None)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=_WORKER_JOIN_TIMEOUT_SEC)
        self._stop_process()

    # ------------------------------------------------------------------
    # 内部：子进程健康检测 & 自动重启
    # ------------------------------------------------------------------

    def _check_process_alive(self) -> bool:
        """检查子进程是否存活。"""
        sdk = self._sdk
        if sdk is None:
            return False
        try:
            proc = sdk._client._proc
        except Exception:
            return False
        return proc is not None and proc.poll() is None

    def _maybe_restart(self) -> bool:
        """
        子进程退出时尝试重启（重建 SDK 实例）。
        返回 True 表示已重启成功。
        """
        # B5-01：时间窗口衰减 —— 距离上次崩溃超过窗口期，认为是新一轮
        # 使用，自动清零计数（防止过去一次配置错误的"历史包袱"持续锁死）。
        now = time.time()
        if self._last_restart_attempt_at and (now - self._last_restart_attempt_at) > _RESTART_BUDGET_RESET_SEC:
            self._restart_count = 0
        if self._restart_count >= _MAX_RESTARTS:
            self._last_error = (
                f"Max restarts ({_MAX_RESTARTS}) exceeded, giving up "
                f"（最近一次崩溃：{self._last_crash_at and int(now - self._last_crash_at)}s 前）"
            )
            logger.error(self._last_error)
            return False

        self._restart_count += 1
        self._last_restart_attempt_at = now
        logger.warning(
            "Harness process exited, restarting (attempt %d/%d)",
            self._restart_count,
            _MAX_RESTARTS,
        )
        time.sleep(_RESTART_COOLDOWN_SEC)
        try:
            self._stop_process()  # 回收旧 SDK（若还活着）
            self._start_process()
            return True
        except Exception as e:
            self._last_error = f"Restart failed: {e}"
            logger.error("Restart failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # 内部：工作线程
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        """
        专用工作线程主循环。
        串行处理任务队列，每个任务通过 vendored SDK 执行真实协议调用。
        """
        while self._running:
            try:
                req = self._task_queue.get(timeout=1.0)
            except Empty:
                # 空闲时检查子进程健康（B5-01：标记崩溃时间，便于时间窗口判定）
                if not self._check_process_alive() and self._running:
                    self._last_crash_at = time.time()
                    self._maybe_restart()
                continue

            if req is None:
                break  # shutdown 信号

            with self._active_lock:
                self._active_request = req
            try:
                result = self._execute_rpc(req.method, req.params, req.timeout)
                self._resolve_future(req, result, None)
            except Exception as e:
                logger.error("RPC call '%s' failed: %s", req.method, e)
                # 如果是子进程崩溃导致的错误，尝试受控重启；请求收到明确异常
                if not self._check_process_alive() and self._running:
                    restarted = self._maybe_restart()
                    err_msg = str(e)
                    if not restarted:
                        err_msg = f"{e} (process crashed, restart failed)"
                    self._resolve_future(req, None, RuntimeError(err_msg))
                else:
                    self._resolve_future(req, None, e)
            finally:
                with self._active_lock:
                    if self._active_request is req:
                        self._active_request = None

    def _execute_rpc(
        self, method: str, params: Dict[str, Any], timeout: float = _DEFAULT_CALL_TIMEOUT_SEC
    ) -> Any:
        """
        通过 vendored SDK 执行一次真实 JSON-RPC 协议调用。

        真实协议仅支持 initialize / session/prompt / shutdown；其他方法名
        一律明确失败（fail-closed），绝不静默回退或猜测协议。

        - ``session/prompt`` 参数：
            sessionId: str
            contentBlocks: list[{"type": "text", "text": ...}]（或 text: str）
          返回：一次完整回合结果 dict：
            {sessionId, finalResponse, finishReason, events, cancelled}
          （内部按请求 ID 匹配响应；notification 独立分发；等待
            agent/inbox/spliced 回执与 session.status=idle 后才返回；
            整个回合受 ``timeout`` 约束）
        - ``shutdown``：返回 {"ok": True}（进程由 _stop_process 真正回收）
        """
        sdk = self._sdk
        if sdk is None:
            raise RuntimeError("Harness SDK runtime not available")

        if method == "initialize":
            # 已在进程启动时完成；幂等返回
            return {"ok": True, "initialized": True}

        if method == "shutdown":
            return {"ok": True}

        if method == "session/prompt":
            session_id = params.get("sessionId") or params.get("session_id")
            if not session_id:
                raise ValueError("session/prompt 缺少 sessionId")
            content_blocks = params.get("contentBlocks")
            if content_blocks is None and params.get("text"):
                content_blocks = [{"type": "text", "text": params["text"]}]
            if not content_blocks:
                raise ValueError("session/prompt 缺少 contentBlocks/text")

            return self._run_turn(
                str(session_id), content_blocks, timeout=timeout,
            )

        raise RuntimeError(
            f"不支持的 JSON-RPC 方法 {method!r}；真实协议仅支持 "
            f"initialize/session/prompt/shutdown（不再提供 session/create、"
            f"session/send、session/close、session/cancel）"
        )

    def _run_turn(
        self, session_id: str, content_blocks: List[Dict[str, Any]],
        *, timeout: float,
    ) -> Dict[str, Any]:
        """执行一次会话回合（SDK Session.run 语义）。

        - session/prompt 只返回 messageId；
        - 最终答案来自 notification 流：agent/inbox/spliced 回执 →
          session.event（收集 events）→ session.status=idle 结束；
        - 回合期间所有 notification 实时转发给事件投影（_dispatch_notification）；
        - 若该会话已被 cancel_session 标记，回合在下一个 notification 上
          提前终止（明确异常），不再返回最终答案。
        """
        sdk = self._sdk
        if sdk is None:
            raise RuntimeError("Harness process runtime not available")

        import threading as _threading

        deadline = time.monotonic() + timeout if timeout and timeout > 0 else None

        def check_turn_control() -> None:
            if self._is_session_cancelled(session_id):
                raise _SessionCancelled(f"会话 {session_id} 已被取消")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Harness 回合超时（{timeout}s，session={session_id}）"
                )

        try:
            check_turn_control()
            harness_session = sdk.start_session(session_id)

            def on_notification(n) -> None:
                # n: deepseek_harness.models.Notification（.method/.payload）
                # SDK 只在收到通知时可协作检查；外层 call() 仍负责兜底超时。
                check_turn_control()
                method = getattr(n, "method", "")
                payload = getattr(n, "payload", {})
                if method:
                    self._dispatch_notification(method, payload)

            run_result = harness_session.run(
                content_blocks,
                on_notification=_notify_wrapper(on_notification),
            )
            # 防止 SDK 在最后一个通知后才返回，越过调用方声明的预算。
            check_turn_control()
        except _SessionCancelled as exc:
            logger.info("Harness 回合被取消（会话 %s）", session_id)
            raise RuntimeError(str(exc)) from exc
        except TimeoutError:
            raise TimeoutError(
                f"Harness 回合超时（{timeout}s，session={session_id}）"
            ) from None
        finally:
            # 取消标记只属于这一回合，允许同一 Harness session 后续继续对话。
            self._clear_session_cancelled(session_id)

        final_text = getattr(run_result, "final_response", "") or ""
        finish_reason = getattr(run_result, "finish_reason", None)

        result = {
            "sessionId": session_id,
            "finalResponse": final_text,
            "finishReason": finish_reason,
            "cancelled": False,
        }
        # The SDK owns the complete turn interval, including the final model call.
        # Do not depend on asynchronous live projections having finished yet.
        from .harness_event_projector import HarnessEventProjector
        from .harness_adapter import HarnessRunAdapter
        usage_events = []
        projector = HarnessEventProjector()
        projector.on_event(lambda event: usage_events.append(event)
                           if event.event_type == "usage_updated" else None)
        for event in getattr(run_result, "events", None) or []:
            projector.project("session.event", {"sessionId": session_id, "event": event})
        records = HarnessRunAdapter._extract_usage_records(usage_events)
        if records:
            result["usage_records"] = records
        return result

    def _resolve_future(
        self,
        req: _TaskRequest,
        result: Any,
        error: Optional[Exception],
    ) -> None:
        """安全地把结果/异常回传到 asyncio Future。"""
        def _settle():
            if error is not None:
                if not req.future.done():
                    req.future.set_exception(error)
            else:
                if not req.future.done():
                    req.future.set_result(result)

        try:
            req.loop.call_soon_threadsafe(_settle)
        except RuntimeError:
            # loop 已关闭
            pass

    # ------------------------------------------------------------------
    # 内部：事件转发（session.event 等 notification）
    # ------------------------------------------------------------------

    def _dispatch_notification(self, method: str, params: Dict[str, Any]) -> None:
        """
        分发 notification（SDK Notification.method/payload）到已注册 callback。

        notification 形如 {"method": "session.event", "payload": {...}}。
        """
        if not method:
            return

        logger.debug("Harness notification: %s", method)

        cb = self._event_callback
        if cb is not None:
            try:
                ret = cb(method, params)
                # 如果 callback 返回协程，在事件循环上调度
                if inspect.iscoroutine(ret):
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(ret, self._loop)
                    else:
                        logger.warning(
                            "Event callback returned coroutine but no running loop: %s",
                            method,
                        )
            except Exception:
                logger.exception(
                    "Event callback raised for notification %s", method
                )

    def register_event_callback(
        self,
        callback: Callable[[str, Dict[str, Any]], Any],
    ) -> None:
        """
        注册事件回调。当 Harness 发送通知类消息时调用。
        callback(event_name, event_data)
        """
        self._event_callback = callback


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_harness_manager_instance: Optional[Any] = None


def get_harness_manager() -> Optional[Any]:
    """获取全局 HarnessManager 实例（可能为 None）。"""
    return _harness_manager_instance


def set_harness_manager(manager: Optional[Any]) -> None:
    """设置全局 HarnessManager 或 HarnessPool 实例。"""
    global _harness_manager_instance
    _harness_manager_instance = manager
