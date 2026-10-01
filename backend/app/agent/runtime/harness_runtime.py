"""Harness 生产运行接线 (B2-01)

AGENT_RUNTIME=harness 时，HarnessManager 是唯一正式生产入口：
- FastAPI lifespan 创建/配置/启动/注册事件回调/shutdown；
- 常驻 JSON-RPC 子进程复用（同一生命周期内进程不重建）；
- session.event 等 notification 通过 HarnessEventProjector 投影到
  analysis_run_events 与活跃 TaskRegistry（SSE/轮询链路）；
- 未配置（无 API Key）时 manager 不启动，运行入口明确失败，不静默回退；
- AGENT_RUNTIME=harness-http 仅作为显式兼容回滚开关（历史 HTTP 适配器）；
- AGENT_RUNTIME=legacy 保持 Python Agent 回滚路径。

真实协议（vendored deepseek_harness SDK 客户端，EEPROM 只支持）：
- initialize / session/prompt / shutdown；
- session/prompt 只返回 messageId，最终答案来自会话 notification
  （agent/inbox/spliced 回执 → session.event → session.status=idle），
  由 SDK Session.run 封装——本模块不自行实现协议。

本模块不读取、不打印任何 API Key。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

# 本文件位于 backend/app/agent/runtime/ → parents[4] 为仓库根（workbench）
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _bundled_assets_root() -> Path:
    """PyInstaller 打包后数据文件根目录。

    macOS onedir --windowed 下，纯 Python 模块收进 PYZ（__file__ 虚拟），
    数据文件（--add-data）落在 Contents/Resources；Windows/Linux 落 _MEIPASS。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        base = Path(meipass)
        resources = base.parent / "Resources"
        if resources.is_dir():
            return resources
        return base
    return PROJECT_ROOT


def _harness_tree() -> Path:
    """定位 Harness 运行时树（打包/源码双布局）。

    优先级：
    1. teachmate_runtime.harness_root()：PyInstaller 打包感知 —— 打包后指向
       Contents/Resources/teachmate-runtime/harness，源码时指向
       <root>/vendor/deepseek-harness-upstream（与下方源码布局相同）。
    2. 回退：<PROJECT_ROOT>/vendor/deepseek-harness-upstream（源码开发模式）。
    """
    try:
        from teachmate_runtime import harness_root
        root = harness_root()
        if root.is_dir():
            return root
    except Exception:
        pass
    return PROJECT_ROOT / "vendor" / "deepseek-harness-upstream"


# vendored Harness 上游仓库根（历史常量：源码布局；新代码请用 _harness_tree()）
_HARNESS_REPO = PROJECT_ROOT / "vendor" / "deepseek-harness-upstream"


def is_harness_mode() -> bool:
    """AGENT_RUNTIME=harness（正式常驻模式）。"""
    return os.getenv("AGENT_RUNTIME", "legacy").strip().lower() == "harness"


def is_harness_http_mode() -> bool:
    """AGENT_RUNTIME=harness-http：显式回退到历史 HTTP 适配器（仅回滚）。"""
    return os.getenv("AGENT_RUNTIME", "legacy").strip().lower() == "harness-http"


def _harness_ai_node_modules(repo: Path) -> Path:
    """定位 @deepseek-ai 插件闭包目录（打包/源码双布局）。

    源码 vendored：<repo>/python/sdk-runtime/node_modules/@deepseek-ai
    打包 teachmate-runtime：<repo>/node_modules/@deepseek-ai（pnpm deploy 产物）
    """
    candidates = (
        repo / "python" / "sdk-runtime" / "node_modules" / "@deepseek-ai",
        repo / "node_modules" / "@deepseek-ai",
    )
    for cand in candidates:
        if cand.is_dir():
            return cand
    return candidates[0]


def locate_harness_asset() -> tuple[str, str]:
    """定位 vendored Harness 运行时入口与 cordis 配置。

    与 vendored 真实布局对齐：
    - 运行时入口：python/sdk-runtime/.../@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/bin.js
      （系统 node 执行；仓库内自带完整插件闭包，无需额外安装）
      打包产物为 teachmate-runtime/harness/node_modules/@deepseek-ai/... 同构路径
    - 配置：examples/jsonrpc-agent/cordis.yml（真实 LLM 组合，含
      sdk-jsonrpc-server + llm-deepseek + sandbox）

    :return: (runtime_bin, cordis_path)
    """
    repo = _harness_tree()
    ai_nm = _harness_ai_node_modules(repo)
    bin_js = ai_nm / "dsh-sdk-jsonrpc-demo" / "lib" / "bin.js"
    if not bin_js.is_file():
        # 保留旧示例目录兜底，避免仓库布局变化时直接崩溃
        alt = (
            repo / "examples" / "node_modules"
            / "@deepseek-ai" / "dsh-sdk-jsonrpc-demo" / "lib" / "bin.js"
        )
        if alt.is_file():
            bin_js = alt
        else:
            logger.error("缺少 vendored Harness 运行时入口: %s", bin_js)
            return "", ""

    cordis = repo / "examples" / "jsonrpc-agent" / "cordis.yml"
    if not cordis.is_file():
        # 打包 teachmate-runtime 布局：examples/teachmate/cordis.yml
        cordis = repo / "examples" / "teachmate" / "cordis.yml"
    if not cordis.is_file():
        # cordis 缺失只警告（运行时实际使用 select_agent_cordis 生成的
        # TeachMate 专用配置），不影响返回 bin_js。
        logger.warning("vendored Harness cordis 未找到（将使用运行时生成的 TeachMate 配置）")

    return str(bin_js), str(cordis)


def locate_runtime_launch() -> tuple[str, str | None, str]:
    """官方运行时定位（B3-01）：优先官方产物，缺失时显式回退 vendored 闭包。

    定位优先级：
    1. ``deepseek_harness_runtime.resolve_bundled_launch_args()``（官方产物：
       exe 单文件 → dev node 闭包）；成功 → ``(runtime_bin="",
       launch_args_override=argv, cordis_path)``；
    2. 官方产物缺失（FileNotFoundError/ImportError）→ 回退 vendored repo
       ``bin.js`` → ``(runtime_bin=str(bin.js), launch_args_override=None,
       cordis_path)``；
    3. 全部缺失 → 返回 ("", None, "")（调用方按未配置处理，不猜路径）。

    :return: (runtime_bin, launch_args_override, cordis_path)
    """
    # 1. 官方定位器
    try:
        from deepseek_harness_runtime import resolve_bundled_launch_args
        from deepseek_harness_runtime import bundled_default_config_path
        args = resolve_bundled_launch_args()
        cordis = str(bundled_default_config_path())
        return "", args, cordis
    except (ImportError, FileNotFoundError) as exc:
        logger.debug("官方 Harness 运行时产物不可用，回退 vendored 闭包: %s", exc)

    # 2. vendored repo 闭包回退（cordis 使用 TeachMate 专用配置）
    bin_js, _cordis = locate_harness_asset()
    return bin_js, None, _cordis


def select_agent_cordis(data_dir: Path | str | None = None) -> str:
    """选择 Harness 的 Cordis 配置（B3-03/验收修复 + P1-4 fail-closed）。

    仅使用运行时生成的 TeachMate 专用 cordis（插件入口以 file:// 绝对路径指向
    vendored 闭包与 TeachMate 白名单插件，规避 ESM 包名解析目录问题）。
    **生成失败必须返回空字符串（fail-closed）**：绝不回退上游示例配置，
    因为示例配置会重新暴露 bash/fs/subagent 等通用工具。

    :return: cordis 文件路径；生成失败返回 ""（调用方按配置错误处理）。
    """
    if data_dir is None:
        logger.error("select_agent_cordis 需要 data_dir（fail-closed）")
        return ""
    try:
        return str(write_teachmate_cordis(data_dir))
    except Exception:
        # P1-4：生成失败禁止静默回退到通用 Harness 配置
        logger.error(
            "TeachMate cordis 生成失败（fail-closed，不启动通用 Harness）",
            exc_info=True,
        )
        return ""


def _render_teachmate_cordis() -> str:
    """渲染 TeachMate 专用 cordis 配置文本（B3-03 / 验收修复）。

    Node ESM 的包解析以 cordis 文件所在目录为基准向上找 node_modules；
    backend 侧没有 @deepseek-ai 闭包。因此把每个插件入口渲成
    ``file://<vendor>/.../lib/index.js`` 绝对 URL（vendored 结构固定，
    不修改 vendor 源码；发布包自带 vendor 即一致）。
    """
    node_modules = _harness_ai_node_modules(_harness_tree())

    def pkg(name: str) -> str:
        # Keep local package identity characters (notably ``@`` and ``+``)
        # readable in the generated URI.  Cordis accepts these as valid URL
        # path characters, and the unescaped path also remains directly
        # inspectable by the release/whitelist checks.  Other characters,
        # including spaces, stay percent-encoded for a valid file URL.
        resolved = str((node_modules / name / "lib" / "index.js").resolve())
        return "file://" + quote(resolved, safe="/:@+~!$&'()*,-.;=_")

    lines: list[str] = []
    lines.append("# TeachMate 专用 Harness 配置（B3-03，运行时生成，勿手改）")
    lines.append("")
    lines.append("- id: sdk-jsonrpc-server")
    lines.append(f"  name: '{pkg('dsh-sdk-jsonrpc-server')}'")
    lines.append("  config:")
    lines.append('    maxTokensAsSuccess: !!js "process.env.DSH_MAX_TOKENS_AS_SUCCESS === undefined ? true : JSON.parse(process.env.DSH_MAX_TOKENS_AS_SUCCESS)"')
    lines.append("")
    lines.append("- id: llm-deepseek")
    lines.append(f"  name: '{pkg('dsh-llm-deepseek')}'")
    lines.append("  config:")
    lines.append("    thinking: !!js \"(process.env.DSH_THINKING === undefined || process.env.DSH_THINKING === 'enabled' || process.env.DSH_THINKING === 'true') ? 'enabled' : 'disabled'\"")
    lines.append('    reasoningEffort: !!js "process.env.DSH_REASONING_EFFORT ?? \'high\'"')
    lines.append("")
    # 注意（P1-1 收紧）：不允许出现 subprocess / bash / fs / tool-fs /
    # subagent / tool-subagent / tool-todo / tool-web / tool-cordis 等
    # 非教学插件 —— 模型可见工具只能是 education-bridge 的白名单教学工具。
    lines.append("- id: agent-spine")
    lines.append(f"  name: '{pkg('dsh-agent-spine-demo')}'")
    lines.append("  config:")
    lines.append("    persona: '你是严谨的教学分析助手：只基于工具返回的证据回答；不编造数据；不提及学生真实姓名，只用匿名编号。'")
    lines.append("    workspaceContext: false")
    lines.append("    skills:")
    lines.append("      enabled: false")
    lines.append("    toolBash: false")
    lines.append("    toolJobs: false")
    lines.append("")
    lines.append("- id: sessions")
    lines.append(f"  name: '{pkg('dsh-session-persistence-jsonl')}'")
    lines.append("  config:")
    lines.append('    root: !!js "process.env.DSH_SESSION_ROOT ?? \'./.sessions\'"')
    lines.append("    compression: 'none'")
    lines.append("")
    lines.append("- id: session-checkpoints")
    lines.append(f"  name: '{pkg('dsh-session-checkpoint-policy')}'")
    lines.append("")
    lines.append("- id: token-meter")
    lines.append(f"  name: '{pkg('dsh-token-meter')}'")
    lines.append("")
    lines.append("- id: compaction-basic")
    lines.append(f"  name: '{pkg('dsh-compaction-basic')}'")
    lines.append("  config:")
    # Harness 仅负责单次分析运行，但异常工具循环仍可能快速放大上下文。
    # 128k 模型按 25%（约 32k）提前压缩，并只保留最近约 6k token；
    # 摘要本身限制为 1.5k，避免“压缩调用”反过来产生过长输出。
    lines.append("    thresholdRatio: 0.25")
    lines.append("    retainTokens: 6000")
    lines.append("    maxTokens: 1536")
    lines.append("    compactionRetries: 1")
    lines.append("")
    # 工具运行时（ToolRuntime 服务提供者，Education Bridge 依赖它）
    lines.append("- id: tools")
    lines.append(f"  name: '{pkg('dsh-tools')}'")
    lines.append("")
    lines.append("- id: education-bridge")
    # 打包/源码双布局：教育桥插件 .mjs 数据文件随 app 分发。
    bridge_candidates = (
        _bundled_assets_root()
        / "backend" / "app" / "agent" / "education_bridge" / "harness_plugin" / "index.mjs",
        PROJECT_ROOT
        / "backend" / "app" / "agent" / "education_bridge" / "harness_plugin" / "index.mjs",
    )
    bridge_plugin = next((p for p in bridge_candidates if p.is_file()), None)
    if bridge_plugin is None:
        raise FileNotFoundError(f"education-bridge 插件缺失: {bridge_candidates[0]}")
    lines.append(f"  name: '{bridge_plugin.as_uri()}'")
    lines.append("  inject:")
    lines.append("    - tools")
    lines.append("  config:")
    lines.append('    scopeRoot: !!js "process.env.DSH_EDUCATION_SCOPE_ROOT ?? \'./.scopes\'"')
    lines.append('    pythonBin: !!js "process.env.DSH_BRIDGE_PYTHON ?? \'python3\'"')
    lines.append("")
    return "\n".join(lines)


def write_teachmate_cordis(data_dir: Path | str) -> Path:
    """把运行时生成的 TeachMate cordis 写入 data_dir（每次启动重新生成）。"""
    target_dir = Path(data_dir) / "harness-cordis"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "teachmate.cordis.yml"
    target.write_text(_render_teachmate_cordis(), encoding="utf-8")
    return target


def build_harness_config(settings: Any, agent_cfg: Any) -> Any:
    """构建 HarnessConfig；未配置 API Key 或资产缺失时返回 None。"""
    from .harness_manager import HarnessConfig
    from ..token_budget import resolve_token_budget

    api_key = getattr(agent_cfg, "text_api_key", None) or None
    if not api_key:
        logger.info("Harness 未配置：缺少文本模型 API Key（health.configured=False）")
        return None

    # B3-01：官方运行时定位（优先官方产物，缺失回退 vendored 闭包）
    runtime_bin, launch_args_override, _fallback_cordis = locate_runtime_launch()
    if not runtime_bin and not launch_args_override:
        logger.warning("Harness vendored 资产缺失，无法启动常驻运行时")
        return None

    # B3-03/验收：使用 TeachMate 专用 cordis（运行时生成，白名单教育工具）
    cordis_path = select_agent_cordis(settings.data_dir)
    if not cordis_path:
        logger.warning("Harness cordis 配置缺失，无法启动常驻运行时")
        return None

    node = shutil.which("node") or "node"
    if not launch_args_override and runtime_bin:
        from teachmate_runtime import find_node
        node = find_node()
        launch_args_override = (node, runtime_bin)
    session_root = Path(settings.data_dir) / "harness-sessions"
    session_root.mkdir(parents=True, exist_ok=True)

    # B3-03：Education Bridge scope 保管目录 + 插件注册
    scope_root = Path(settings.data_dir) / "harness-session-scopes"
    scope_root.mkdir(parents=True, exist_ok=True)
    # 打包/源码双布局：教育桥插件 .mjs 数据文件随 app 分发。
    bridge_candidates = (
        _bundled_assets_root()
        / "backend" / "app" / "agent" / "education_bridge" / "harness_plugin" / "index.mjs",
        Path(__file__).resolve().parents[2]
        / "agent" / "education_bridge" / "harness_plugin" / "index.mjs",
    )
    bridge_plugin = next((p for p in bridge_candidates if p.is_file()), None)
    if bridge_plugin is not None:
        plugin_spec = bridge_plugin.resolve().as_uri()
    else:
        plugin_spec = ""

    # 插件解析：Node 从 bin.js 沿 node_modules 向上解析，vendored 闭包
    # 自带全部 @deepseek-ai/* 插件；pnpm 商店目录作为兜底 NODE_PATH。
    harness_tree = _harness_tree()
    pnpm_store = harness_tree / "node_modules" / ".pnpm" / "node_modules"
    extra_env: dict[str, str] = {}
    if pnpm_store.is_dir():
        extra_env["NODE_PATH"] = str(pnpm_store)
    if scope_root.is_dir():
        extra_env["DSH_EDUCATION_SCOPE_ROOT"] = str(scope_root)
    # Education Bridge 数据目录（workbench.db 所在），供 bridge_cli 只读查询
    extra_env["DSH_EDUCATION_DATA_DIR"] = str(Path(settings.data_dir))
    if bridge_plugin is not None:
        extra_env["DSH_EDUCATION_BRIDGE_PLUGIN"] = bridge_plugin.resolve().as_uri()
    extra_env["DSH_BRIDGE_PYTHON"] = sys.executable
    # P1-XX：thinking 与 reasoning_effort 必须成对合法，且映射到 llm-deepseek
    # 插件 schema 允许的集合 {"off","high","max"}：
    #   - thinking 关闭 → reasoningEffort 强制 "off"（传 "disabled" 会校验失败崩溃）；
    #   - low/medium 等兼容接口档位 → 统一映射 "high"（SDK 文档：medium 兼容映射 high）。
    _thinking = "enabled" if getattr(agent_cfg, "text_thinking_enabled", True) else "disabled"
    extra_env["DSH_THINKING"] = _thinking
    _effort = getattr(agent_cfg, "text_reasoning_effort", "high") or "high"
    if _effort == "disabled":
        _effort = "off"
    elif _effort not in {"off", "high", "max"}:
        _effort = "high"
    extra_env["DSH_REASONING_EFFORT"] = _effort if _thinking == "enabled" else "off"
    token_budget = resolve_token_budget(agent_cfg)

    return HarnessConfig(
        cordis_path=cordis_path,
        session_root=str(session_root),
        model=(getattr(agent_cfg, "text_model_name", None) or "deepseek-chat"),
        max_tokens=token_budget.output_limit,
        api_base=(
            getattr(agent_cfg, "text_api_base_url", None)
            or "https://api.deepseek.com/v1"
        ),
        api_key=api_key,
        node_path=node,
        runtime_bin=runtime_bin,
        launch_args_override=launch_args_override,
        scope_root=str(scope_root),
        config_version=getattr(agent_cfg, "config_version", 1),
        extra_env=extra_env,
    )


def setup_harness_manager(settings: Any, agent_cfg: Any) -> Any:
    """建立全局 HarnessManager/Pool（未配置时返回 None，不创建进程）。"""
    from .harness_manager import (
        get_harness_manager,
        set_harness_manager,
    )

    existing = get_harness_manager()
    if existing is not None:
        return existing

    config = build_harness_config(settings, agent_cfg)
    if config is None:
        return None

    # 默认启用四个独立 Harness 槽位；可通过环境变量调低池大小，
    # 为低配设备保留可控的资源开销。每个槽位都有独立进程和 scope 根目录。
    import os
    pool_size = os.getenv("TEACHMATE_HARNESS_POOL_SIZE", "4")
    if str(pool_size).strip() not in {"", "0", "1"}:
        from .harness_pool import HarnessPool
        manager = HarnessPool(config, size=pool_size)
    else:
        from .harness_manager import HarnessManager
        manager = HarnessManager(config)
    set_harness_manager(manager)
    return manager


def attach_event_projection(manager: Any, session_factory: Any) -> None:
    """把 Harness notification 投影到 analysis_run_events 与活跃 TaskRegistry。"""
    from .harness_event_projector import HarnessEventProjector
    from .event_projection import broadcast_to_active_run

    projector = HarnessEventProjector()
    projector.on_event(
        lambda event: broadcast_to_active_run(event, session_factory)
    )

    def _callback(method: str, params: dict[str, Any]) -> None:
        projector.project(method, params)

    manager.register_event_callback(_callback)
    logger.info("Harness 事件投影已接线（analysis_run_events + TaskRegistry）")
