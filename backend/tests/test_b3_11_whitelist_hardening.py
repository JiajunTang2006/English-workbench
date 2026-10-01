"""P1 收紧：TeachMate cordis 生成器只含白名单插件（B3-03 审查修复）。

背景：B3-03 初版仅关闭了 bash 工具（toolBash: false / toolJobs: false），
但仍加载了 subprocess / bash / fs-local / tool-fs / subagent / tool-subagent /
tool-todo 等非教学插件 —— 模型可见工具不止 8 个教育工具。审查要求：
- 移除文件、子 Agent、Todo 等非教学工具；
- 模型可见工具名称必须恰好等于 8 个教育工具（真实启动断言在 P2 验收脚本）；
- read/write/edit/subagent 调用必须被拒绝（fail-closed）；
- 专用配置生成失败必须 fail-closed（不得回退到上游示例通用配置）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Education Bridge 提供的白名单教育工具
ALLOWED_BRIDGE_TOOLS = {
    "get_practice_context",
    "get_student_learning_evidence",
    "get_original_question",
    "resolve_student",
    "get_exam_analysis_bundle",
    "get_exam_overview",
    "get_score_distribution",
    "get_question_list",
    "get_student_trend",
    "get_risk_signals",
    "get_wrong_questions",
    "get_student_scores",
    "get_formal_attachment",
    "get_teaching_guidance",
    "submit_report",
}

# 生成器允许保留的 cordis 插件 id（系统服务 + education-bridge 宿主）
ALLOWED_PLUGIN_IDS = {
    "sdk-jsonrpc-server",
    "llm-deepseek",
    "agent-spine",
    "sessions",
    "session-checkpoints",
    "token-meter",
    "compaction-basic",
    "tools",
    "education-bridge",
}

# 必须从生成配置中消失的插件包名（缺一不可）
FORBIDDEN_PACKAGE_FRAGMENTS = (
    "dsh-subprocess-local",
    "dsh-bash-local",
    "dsh-fs-local",
    "dsh-fs-observation-policy",
    "dsh-tool-fs",
    "dsh-tool-subagent",
    "dsh-subagent",
    "dsh-subagent-spawn-in-process",
    "dsh-tool-todo",
    "dsh-tool-web",
    "dsh-tool-cordis",
    "dsh-shell",
    "code-runtime",
    "dsh-tool-jobs",
)


def _render(tmp_path) -> str:
    """调用生产生成器输出文本。"""
    from backend.app.agent.runtime.harness_runtime import write_teachmate_cordis

    p = write_teachmate_cordis(tmp_path)
    return p.read_text(encoding="utf-8")


class TestWhitelistHardening:
    def test_no_forbidden_plugins_anywhere(self, tmp_path):
        """禁止插件包不得以任何形式出现在生成配置中（文件路径或包名）。"""
        text = _render(tmp_path)
        for frag in FORBIDDEN_PACKAGE_FRAGMENTS:
            assert frag not in text, f"生成 cordis 不应包含 {frag}"

    def test_plugin_set_is_exactly_whitelist(self, tmp_path):
        """生成配置的插件 id 集合必须恰好等于白名单（不多不少）。"""
        text = _render(tmp_path)
        ids = re.findall(r"^- id: (\S+)", text, re.M)
        assert set(ids) == ALLOWED_PLUGIN_IDS, f"插件集合偏离白名单: {ids}"

    def test_agent_spine_hardened(self, tmp_path):
        """agent-spine 必须关闭 bash/jobs/workspace/skills。"""
        text = _render(tmp_path)
        assert "toolBash: false" in text
        assert "toolJobs: false" in text
        assert "workspaceContext: false" in text
        assert "skills:" in text and "enabled: false" in text

    def test_bridge_tool_defs_are_education_only(self, tmp_path):
        """插件 TOOL_DEFS 恰好等于白名单教学工具（跨语言一致性）。"""
        import json
        import os
        import subprocess
        import sys

        plugin = (
            BACKEND / "app" / "agent" / "education_bridge" / "harness_plugin"
            / "index.mjs"
        )
        script = (
            f"import {{TOOL_DEFS}} from '{plugin.resolve().as_uri()}';"
            "console.log(JSON.stringify(TOOL_DEFS.map(d => d.name)))"
        )
        out = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert out.returncode == 0, out.stderr
        names = set(json.loads(out.stdout.strip()))
        assert names == ALLOWED_BRIDGE_TOOLS, f"工具集合偏离白名单: {names}"

    def test_bridge_cli_rejects_non_whitelist_tool(self, tmp_path):
        """bridge CLI 对 read/write/edit/subagent 必须 fail-closed。"""
        import json
        import os
        import subprocess
        import sys

        scope = tmp_path / "scope.json"
        scope.write_text(json.dumps({"run_id": 1, "term_id": 1}), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(BACKEND.parent)}
        for tool in ("read_file", "write_file", "edit_file", "subagent",
                     "bash", "get_shell"):
            p = subprocess.run(
                [sys.executable, "-m",
                 "backend.app.agent.education_bridge.bridge_cli",
                 "--tool", tool, "--data-dir", str(tmp_path),
                 "--db", str(tmp_path / "workbench.db"),
                 "--scope", str(scope), "--args", "{}"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
                env=env,
            )
            assert p.returncode != 0, f"{tool} 应被拒绝"
            # argparse choices 白名单：非白名单工具直接 usage 报错退出（fail-closed）
            assert ("invalid choice" in p.stderr or "usage:" in p.stderr), \
                f"{tool} 拒绝信息缺失: {p.stderr[:160]}"
            # 且 usage 中暴露的选择集恰好是白名单教学工具
            for allowed in sorted(ALLOWED_BRIDGE_TOOLS):
                assert allowed in p.stderr, f"白名单工具 {allowed} 未出现在 choices"

    def test_cordis_generation_failure_is_fail_closed(self, tmp_path, monkeypatch):
        """生成失败必须返回空（fail-closed），绝不回退上游示例配置。"""
        from backend.app.agent.runtime import harness_runtime as hr

        def _boom(name: str) -> str:
            raise RuntimeError("plugin lookup failed")

        monkeypatch.setattr(hr, "_render_teachmate_cordis", _boom)
        result = hr.select_agent_cordis(tmp_path)
        assert result == ""

    def test_cordis_generated_plugin_files_exist(self, tmp_path):
        """生成配置中所有 file:// 插件入口必须真实存在（防加载失败兜底）。"""
        text = _render(tmp_path)
        uris = re.findall(r"name: 'file://([^']+)'", text)
        assert len(uris) >= 8
        for u in uris:
            p = Path(u)
            assert p.is_file(), f"插件入口缺失: {p}"

def test_thinking_env_switch_present(tmp_path):
    """llm 段支持 DSH_THINKING env 开关，默认 enabled 与历史行为一致。"""
    from backend.app.agent.runtime.harness_runtime import _render_teachmate_cordis

    text = _render_teachmate_cordis()
    assert "DSH_THINKING" in text
    assert "? 'enabled' : 'disabled'" in text
    assert "thinking: !!js" in text


def test_vendored_llm_deepseek_tool_frames_patch_present():
    """vendored dsh-llm-deepseek 必须保留对 OpenAI 兼容网关流式工具帧的兼容补丁：
    仅接受非空 name/id、已设置值不被后续 null/空帧覆盖。否则 new-api/one-api 网关
    （如 modelhub）会在中间帧下发 {name:null}/{id:""}，把已正确的工具名清空，
    回到 UNKNOWN_TOOL 空名循环（B3 真机验收已踩坑）。

    补丁固化于源码 src/translate.ts（构建后产物由编译生成，注释标记以源码为准，
    产物则断言编译后逻辑仍在）。"""
    root = (Path(__file__).resolve().parents[2]
            / "vendor" / "deepseek-harness-upstream" / "packages"
            / "llm" / "llm-deepseek")
    src = root / "src" / "translate.ts"
    src_text = src.read_text(encoding="utf-8")
    assert "兼容性修复" in src_text  # 补丁标记在源码（构建源头）中必须存在
    assert "typeof call.id === 'string' && call.id.length > 0" in src_text
    assert "block.name === undefined" in src_text
    assert "call.function.name.length > 0" in src_text

    idx = root / "lib" / "index.js"
    text = idx.read_text(encoding="utf-8")
    assert "typeof call.id === \"string\" && call.id.length > 0" in text
    assert "block.name === void 0 && typeof call.function?.name" in text
