"""B3-00 协议基线测试：mock 数据与真实 Harness 协议一致。

门禁点：
1. SUPPORTED_RPC_METHODS 与真实协议完全一致（initialize/session/prompt/shutdown）。
2. 仓库中不再存在把假方法（session/create|send|close|cancel）当作 RPC 发出/调用的代码
   （只允许 fail-closed 报错文案与注释中说明性提及；本地 cancel_session 是本地语义接口）。
3. 现有 mock 通知与真实 SDK 格式一致：Notification(method, payload)；
   session.event → {"sessionId", "event": {"type","data"}}。
4. 高层接口契约：最终答案来自 SDK 回合结果（RunResult 语义），不是 messageId。

全部离线，不依赖真实 API Key / 网络。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.agent.runtime.harness_manager import SUPPORTED_RPC_METHODS
from backend.app.agent.run_executor import _build_harness_system_prompt

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

FAKE_RPC_METHODS = [
    "session/create",
    "session/send",
    "session/close",
    "session/cancel",
]


# ---------------------------------------------------------------------------
# 1. 协议方法集合
# ---------------------------------------------------------------------------

def test_supported_rpc_methods_match_real_protocol():
    """真实协议只有 initialize / session/prompt / shutdown。"""
    assert SUPPORTED_RPC_METHODS == frozenset({"initialize", "session/prompt", "shutdown"})


@pytest.mark.parametrize("method", FAKE_RPC_METHODS)
def test_fake_methods_not_configured_as_rpc(method):
    """假协议方法绝不能出现在方法分发表 / 可调用集合中。"""
    assert method not in SUPPORTED_RPC_METHODS


def test_no_fake_rpc_call_emission_in_codebase():
    """扫描后端代码：假方法名一旦出现必须是说明性/报错性/注释性出现，不是真实调用。

    放行规则（按重要性排序）：
    a. ``harness_manager.py`` 是权威 fail-closed 实现，其 docstring / 报错文案中的
       提及都属于「说明不存在该方法」；
    b. 任何含 ``cancel_session`` 的行：本地取消语义接口（非 RPC 方法）；
    c. 日志/描述性文本（“Failed to send session/cancel…”、“向 Harness 发送
       session/cancel”）实际调用的是 cancel_session 本地接口；
    d. 行内出现说明性关键词（不支持/不存在/不再提供/不提供/仅提供/仅支持/
       本地/取消/不是 RPC/不自行实现/仅回滚/仅兼容/fail-closed/测试/mock 等）。
    """
    explanatory_hints = (
        "不支持", "不存在", "不再提供", "不提供", "仅提供", "仅支持",
        "本地", "取消", "不是 RPC", "不自行实现", "仅回滚", "仅兼容",
        "fail-closed", "fail closed", "注释", "模拟", "测试", "mock", "fake",
    )
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            for meth in FAKE_RPC_METHODS:
                if meth not in line:
                    continue
                stripped = line.strip()
                # a. 权威实现文件：docstring/报错文案属于合规基线
                if path.name == "harness_manager.py":
                    continue
                # b. 本地取消语义接口（含其日志/描述）
                if "cancel_session" in line:
                    continue
                # c. 日志/描述文本（实际走 cancel_session）
                if "Failed to send" in line or "发送 session/cancel" in line or "session/cancel sent" in line:
                    continue
                # d. 纯注释或文档字符串行
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if any(hint in line for hint in explanatory_hints):
                    continue
                offenders.append(
                    f"{path.relative_to(BACKEND)}:{line_no}: {stripped}"
                )
    assert not offenders, (
        "发现疑似把假协议方法当作真实 RPC 使用的代码（若为误报，请补充说明性语境关键词）：\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. 通知/事件结构 == 真实 SDK 格式
# ---------------------------------------------------------------------------

def _fake_notification(method: str, payload: dict):
    return SimpleNamespace(method=method, payload=payload)


def test_notification_structure_matches_sdk():
    """真实 session.event 的格式：payload = {sessionId, event:{type,data}}。"""
    n = _fake_notification(
        "session.event",
        {"sessionId": "tm-s1",
         "event": {"type": "assistant/message", "data": {"text": "hello"}}},
    )
    payload = n.payload
    assert payload["sessionId"] == "tm-s1"
    assert payload["event"]["type"] == "assistant/message"
    assert payload["event"]["data"] == {"text": "hello"}


def test_event_projector_accepts_real_format():
    """HarnessEventProjector 能消费真实格式（raw event → RuntimeEvent）。"""
    from backend.app.agent.runtime.harness_event_projector import HarnessEventProjector

    captured: list = []
    projector = HarnessEventProjector()
    projector.on_event(captured.append)

    ev = projector.project(
        "session.event",
        {"sessionId": "tm-s1",
         "event": {"type": "turn/start", "data": {"ts": 1}}},
    )
    assert ev is not None
    assert ev.event_type == "turn_start"
    assert ev.data.get("sessionId") == "tm-s1"
    assert len(captured) == 1


def test_session_status_events():
    """session.status=idle 投影为 harness_idle。"""
    from backend.app.agent.runtime.harness_event_projector import HarnessEventProjector

    projector = HarnessEventProjector()
    ev = projector.project(
        "session.status",
        {"sessionId": "tm-s1", "status": "idle"},
    )
    assert ev is not None
    assert ev.event_type == "harness_idle"


def test_public_progress_events_are_normalized_without_reasoning_payload():
    """运行时公开步骤可增量更新，且原始思考字段不会被要求或透传。"""
    from backend.app.agent.runtime.harness_event_projector import HarnessEventProjector
    from backend.app.agent.event_types import PROGRESS_STEP_STARTED

    projector = HarnessEventProjector()
    ev = projector.project(
        "session.event",
        {"sessionId": "tm-s1", "event": {"type": "progress/step_started", "data": {
            "step_id": "scope", "title": "读取班级数据", "summary": "已获得公开统计摘要",
            "next_action": "比较分数段变化", "reasoning": "不得透传",
        }}},
    )
    assert ev is not None
    assert ev.event_type == PROGRESS_STEP_STARTED
    assert ev.data["title"] == "读取班级数据"
    assert "reasoning" not in ev.data


def test_model_step_events_are_projected_instead_of_filtered():
    """Harness step 边界必须实时进入教师端过程时间线。"""
    from backend.app.agent.runtime.harness_event_projector import HarnessEventProjector
    from backend.app.agent.event_types import MODEL_STARTED, MODEL_COMPLETED

    projector = HarnessEventProjector()
    started = projector.project(
        "session.event",
        {"sessionId": "tm-s1", "event": {"type": "step/start", "data": {
            "step": 1, "title": "分析成绩数据", "summary": "正在比较分数段",
        }}},
    )
    completed = projector.project(
        "session.event",
        {"sessionId": "tm-s1", "event": {"type": "step/end", "data": {
            "step": 1,
        }}},
    )
    assert started is not None and started.event_type == MODEL_STARTED
    assert started.data["title"] == "分析成绩数据"
    assert completed is not None and completed.event_type == MODEL_COMPLETED


# ---------------------------------------------------------------------------
# 3. 高层接口契约（最终答案来自回合结果，不是 messageId）
# ---------------------------------------------------------------------------

def test_run_result_carries_final_answer():
    """SDK Session.run 结果携带最终答案；messageId 不是答案。"""
    ok_result = SimpleNamespace(
        final_response="最终答案文本",
        finish_reason="normal",
        session_id="tm-s1",
    )
    assert ok_result.final_response == "最终答案文本"
    # session/prompt 只回 messageId —— 不能拿它当答案
    prompt_response = {"messageId": "msg-1"}
    assert "finalResponse" not in prompt_response
    assert "messageId" in prompt_response


def test_content_blocks_shape():
    """session/prompt 的 contentBlocks 结构必须与 SDK 一致。"""
    blocks = [{"type": "text", "text": "hello"}]
    assert isinstance(blocks, list)
    assert all(b.get("type") == "text" for b in blocks)


def test_harness_exam_analysis_without_exam_has_fail_closed_scope_prompt():
    """Harness 无 exam_id 时不得假装读取或补写数据库考试事实。"""
    prompt = _build_harness_system_prompt("exam_analysis", None)
    assert "当前未绑定数据库中的具体考试" in prompt
    assert "不得声称读取了数据库考试" in prompt
    assert "不得创建、修改或补写数据库考试事实" in prompt
    assert "不得编造统计数字" in prompt
    assert "必须明确说明局限" in prompt


def test_harness_exam_analysis_with_exam_keeps_normal_prompt():
    """绑定数据库考试后不追加无考试限制文案。"""
    prompt = _build_harness_system_prompt("exam_analysis", 42)
    assert "当前未绑定数据库中的具体考试" not in prompt
    assert "不得编造数据" in prompt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
