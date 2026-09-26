"""B3-01 官方 Harness SDK 接入测试（含真实本地运行时启动）。

覆盖（离线为主，部分测试真实拉起子进程但绝不调真实 LLM）：
1. 官方运行时定位优先：resolve_bundled_launch_args 可用时使用官方产物；
   缺失时显式回退到 vendored repo 闭包 bin.js（不静默猜测路径）；
2. HarnessConfig 支持 launch_args_override（官方 exe/node 元组），SDK 层消费；
3. 真实子进程冒烟（不依赖真实 API Key）：
   - 真实拉起 node bin.js（或官方 exe）→ SDK initialize 握手 → close 干净退出；
   - close 后进程必须被回收（poll() 非 None），无悬挂子进程；
   - 同一 SDK 实例内两次 start_session 不重建进程（复用）；
4. 健康检查语义：configured 判定要求完整配置，不含 API Key 泄露；
5. 启动失败（资产缺失/配置不完整）显式失败，不静默回退 Python Agent。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
HARNESS_REPO = PROJECT_ROOT / "vendor" / "deepseek-harness-upstream"

# vendored repo 闭包入口（不存在时相关真实进程测试 skip）
_REPO_BIN_JS = (
    HARNESS_REPO / "python" / "sdk-runtime" / "node_modules"
    / "@deepseek-ai" / "dsh-sdk-jsonrpc-demo" / "lib" / "bin.js"
)
_REPO_CORDIS = HARNESS_REPO / "examples" / "jsonrpc-agent" / "cordis.yml"


def _node_available() -> bool:
    return shutil.which("node") is not None


def _repo_runtime_available() -> bool:
    return _REPO_BIN_JS.is_file() and _REPO_CORDIS.is_file() and _node_available()


# ---------------------------------------------------------------------------
# 1. 官方运行时定位（含回退）
# ---------------------------------------------------------------------------

def test_locate_runtime_prefers_official():
    """官方定位器成功时优先使用官方产物，不碰 repo 闭包。"""
    from backend.app.agent.runtime.harness_runtime import locate_runtime_launch

    with mock.patch(
        "deepseek_harness_runtime.resolve_bundled_launch_args",
        return_value=("/opt/dsh-exe",),
    ), mock.patch(
        "deepseek_harness_runtime.bundled_default_config_path",
        return_value=Path("/opt/cordis.yml"),
    ):
        runtime_bin, launch_args, cordis = locate_runtime_launch()
    assert runtime_bin == ""
    assert launch_args == ("/opt/dsh-exe",)
    assert cordis  # 默认配置始终提供


def test_official_runtime_fallback_to_repo_closure():
    """官方产物缺失（FileNotFoundError）→ 显式回退 vendored repo 闭包 bin.js。

    launch_args=None 表示走 runtime_bin 通道（SDK 按单文件启动）。
    """
    from backend.app.agent.runtime.harness_runtime import locate_runtime_launch

    def _raise(*a, **k):
        raise FileNotFoundError("missing official runtime")

    with mock.patch(
        "deepseek_harness_runtime.resolve_bundled_launch_args",
        side_effect=_raise,
    ):
        runtime_bin, cmd, cordis = locate_runtime_launch()
    if not _repo_runtime_available():
        pytest.skip("vendored repo 闭包缺失，回退路径无法真实验证")
    assert cmd is None
    # 双布局：源码 vendored（python/sdk-runtime/...）或打包 teachmate-runtime
    # （node_modules/@deepseek-ai/...）都是有效入口，两者语义等价。
    assert runtime_bin
    assert Path(runtime_bin).is_file()
    assert Path(cordis).is_file()


def test_build_config_surfaces_launch_args():
    """build_harness_config 把官方启动参数透传到 HarnessConfig。"""
    from backend.app.agent.runtime import harness_runtime
    from backend.app.agent.config import get_agent_config

    import tempfile
    tmp = Path(tempfile.mkdtemp())
    settings = SimpleNamespace(data_dir=tmp)

    class _S:
        text_api_key = "sk-test"
        text_model_name = "deepseek-chat"
        text_api_base_url = "https://api.deepseek.com/v1"

    with mock.patch.object(
        harness_runtime, "locate_runtime_launch",
        return_value=("", ("/opt/bin",), "/tmp/cordis.yml"),
    ):
        cfg = harness_runtime.build_harness_config(settings, _S())
    assert cfg is not None
    assert cfg.launch_args_override == ("/opt/bin",)
    assert cfg.runtime_bin == ""
    assert cfg.max_tokens == 4096


# ---------------------------------------------------------------------------
# 2. HarnessManager 配置层：launch_args_override 透传（不启动进程，离线）
# ---------------------------------------------------------------------------

def test_harness_config_carries_launch_args_override():
    from backend.app.agent.runtime.harness_manager import HarnessConfig
    cfg = HarnessConfig(
        cordis_path="/tmp/cordis.yml",
        session_root="/tmp/sessions",
        api_key="sk-test",
        runtime_bin="",
        launch_args_override=("/tmp/exe",),
    )
    assert cfg.is_configured  # 官方 exe 模式无需 runtime_bin 也可判定已配置
    assert cfg.launch_args_override == ("/tmp/exe",)


# ---------------------------------------------------------------------------
# 3. 真实本地 Harness 运行时冒烟（不依赖真实 API Key）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (_REPO_BIN_JS.is_file() and _REPO_CORDIS.is_file() and _node_available()),
    reason="vendored repo 运行时闭包缺失或 node 不可用",
)
def test_real_runtime_initialize_and_close():
    """真实拉起本地 Harness 运行时：SDK start（initialize 握手）→ close 干净退出。

    全程离线（initialize 不做真实 LLM 调用）。close 后子进程必须被回收。
    """
    from deepseek_harness.api import DeepSeekHarness, DeepSeekHarnessConfig

    cfg = DeepSeekHarnessConfig(
        provider="deepseek-official",
        model="deepseek-chat",
        cwd=str(PROJECT_ROOT),
        session_root=str(Path(tempfile.mkdtemp(suffix="-harness-session"))),
        cordis=str(_REPO_CORDIS),
        api_key="sk-bogus-for-local-runtime-test",
        base_url="https://api.deepseek.com/v1",
        runtime_bin=str(_REPO_BIN_JS),
        request_timeout_seconds=20,
        shutdown_timeout_seconds=5,
    )
    harness = DeepSeekHarness(cfg)
    try:
        harness.start()
        proc = harness._client._proc
        assert proc is not None and proc.poll() is None, "运行时子进程未存活"
    finally:
        harness.close()

    # close 后必须回收，无悬挂进程
    assert proc.poll() is not None, "close 后子进程仍存活（悬挂）"


@pytest.mark.skipif(
    not (_REPO_BIN_JS.is_file() and _REPO_CORDIS.is_file() and _node_available()),
    reason="vendored repo 运行时闭包缺失",
)
def test_real_runtime_session_reuse():
    """同一 SDK 实例内 start_session 复用同一子进程，不重建。"""
    from deepseek_harness.api import DeepSeekHarness, DeepSeekHarnessConfig

    cfg = DeepSeekHarnessConfig(
        cwd=str(PROJECT_ROOT),
        session_root=str(Path(tempfile.mkdtemp(suffix="-harness-session"))),
        cordis=str(_REPO_CORDIS),
        api_key="test-bogus-key",
        runtime_bin=str(_REPO_BIN_JS),
        request_timeout_seconds=20,
        shutdown_timeout_seconds=5,
    )
    harness = DeepSeekHarness(cfg)
    try:
        harness.start()
        p1 = harness._client._proc
        s1 = harness.start_session("tm-s1")
        s2 = harness.start_session("tm-s2")
        assert harness._client._proc is p1, "两次 start_session 不应重建子进程"
        assert s1.id != s2.id
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# 4. 健康检查与失败语义
# ---------------------------------------------------------------------------

def test_health_configured_requires_complete_config():
    from backend.app.agent.runtime.harness_manager import HarnessManager
    m = HarnessManager()  # 无配置
    h = m.health()
    assert h.configured is False
    assert h.running is False
    assert h.pid is None


def test_health_does_not_leak_api_key():
    """健康状态不包含任何 Key 信息。"""
    from backend.app.agent.runtime.harness_manager import (
        HarnessManager, HarnessConfig,
    )
    m = HarnessManager(HarnessConfig(
        cordis_path="/tmp/cordis.yml",
        session_root="/tmp/s",
        api_key="sk-secret-key-must-not-leak",
        runtime_bin="/tmp/bin.js",
    ))
    d = m.health().to_dict()
    assert "api_key" not in d and "key" not in d
    s = str(d)
    assert "sk-secret" not in s


def test_unconfigured_start_fails_explicitly():
    """未配置（无 API Key/资产）时 start 必须显式失败，不静默回退。"""
    from backend.app.agent.runtime.harness_manager import HarnessManager
    m = HarnessManager()
    with pytest.raises(RuntimeError):
        asyncio.run(m.start())
