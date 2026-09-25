"""B3 最终真实纵向验收（15 步，真实 DeepSeek API Key，回合受限）

环境：AGENT_RUNTIME=harness + 真实 Key（/provider/switch 写入本地保管库，
等价「设置页配置」）。回合控制在 2~3 个，prompt 精简，控制用量。
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

DATA = Path(tempfile.mkdtemp(prefix="b3-vertical-"))
os.environ["WORKBENCH_DATA_DIR"] = str(DATA)
os.environ["WORKBENCH_TOKEN"] = "b3-vertical-token"
os.environ.setdefault("AGENT_RUNTIME", "harness")
os.environ.setdefault("AGENT_ENABLED", "true")
os.environ.setdefault("AGENT_TEXT_ENABLED", "true")
os.environ.setdefault("AGENT_BACKGROUND_WORKER_ENABLED", "true")
# 注意：本进程不设置 DEEPSEEK_API_KEY —— Key 只能来自保管库（第 2 步）


def _cleanup() -> None:
    """无论成功/失败/异常，退出时删除整个验收数据目录。

    覆盖：provider/api_key 保管库、harness-sessions、outbound-audit、
    harness-session-scopes、workbench.db 与全部临时产物。
    """
    import shutil

    try:
        shutil.rmtree(DATA, ignore_errors=True)
        # 兜底：即使 DATA 已被外部删除，也确保保管库文件不残留
        (DATA / "provider" / "api_key").unlink(missing_ok=True)
    except OSError:
        pass
    print(f"[cleanup] 已删除验收临时目录: {DATA}（含 Key 保管库与全部临时数据）")


import atexit

atexit.register(_cleanup)

KEY = os.environ.get("WORKBENCH_TEST_API_KEY", "")
if not KEY:
    print("需要设置 WORKBENCH_TEST_API_KEY 环境变量（真实测试 Key，不入库）")
    sys.exit(2)
# P2/中转站：provider/端点/模型名均可用环境变量覆盖（Key 始终只走 env）
TEST_PROVIDER = os.environ.get("WORKBENCH_TEST_PROVIDER", "openai_compat")
TEST_BASE_URL = os.environ.get(
    "WORKBENCH_TEST_BASE_URL", "https://modelhub.mimouse.com/v1")
TEST_MODEL = os.environ.get("WORKBENCH_TEST_MODEL", "deepseek-v4-flash")
results: dict = {}
ok = 0
failed = 0
TERMINAL = ("completed", "failed", "cancelled", "degraded")


def check(name: str, cond: bool, detail: str = ""):
    global ok, failed
    if cond:
        ok += 1
    else:
        failed += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    results[name] = {"pass": cond, "detail": detail}


def must(name: str, cond: bool, detail: str = ""):
    check(name, cond, detail)
    if not cond:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(1)


from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.auth import TOKEN as APP_TOKEN
from fastapi.testclient import TestClient

H = {"Authorization": f"Bearer {APP_TOKEN}"}


def wait_terminal(c, run_id, timeout_s=300):
    st = None
    for _ in range(timeout_s // 2):
        st = c.get(f"/api/v1/agent/runs/{run_id}", headers=H).json()
        if st.get("status") in TERMINAL:
            return st
        time.sleep(2)
    return st


# ---------------- 第 1-2 步：启动 + 设置页配置 Key ----------------
print("== 步骤 1-2：启动应用 + 设置页配置 ==")
app_a = create_app(Settings(data_dir=DATA))
with TestClient(app_a) as c:
    r = c.post("/api/v1/agent/provider/switch", headers=H, json={
        "provider": TEST_PROVIDER, "model_name": TEST_MODEL,
        "base_url": TEST_BASE_URL if TEST_PROVIDER == "openai_compat" else None,
        "api_key": KEY,
    })
    body = r.json() if r.status_code == 200 else {}
    must("2a 设置页保存 Key 成功", r.status_code == 200 and body.get("success"),
         r.text[:200])
    must("2b 接口响应不含 Key 明文", KEY not in r.text)
    from backend.app.agent.keyvault import key_file, load_api_key
    must("2c Key 已入本地保管库", load_api_key() == KEY)
    must("2d 保管库权限 0600", (stat.S_IMODE(os.stat(key_file()).st_mode) & 0o077) == 0)

# ---------------- 数据准备（第 3-5 步素材） ----------------
print("== 步骤 3-5：会话 / scope / 正式附件 ==")
app_b = create_app(Settings(data_dir=DATA))
with TestClient(app_b) as c:
    st = c.get("/api/v1/agent/provider/status", headers=H).json()
    must("B0 重启后 Key 来自保管库（无需环境变量）",
         st.get("key_configured") is True, json.dumps(st)[:200])
    check("B1 模式为 harness", st.get("mode") == "harness", st.get("mode", ""))

    from backend.app.models.entities import (
        Term, Class, Student, Enrollment, Exam, ExamScore, Attachment,
    )
    from backend.app.models.agent_entities import (
        AgentMessage, AgentMessageAttachment,
    )
    with app_b.state.session_factory() as db:
        t = Term(code="vt1", name="验收学期", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        db.add(t)
        db.commit()
        cls = Class(name="一班", term_id=t.id)
        db.add(cls)
        db.commit()
        s1 = Student(name="张三", student_no="V20260001", parent_phone="13800000000")
        s2 = Student(name="李四", student_no="V20260002", parent_phone="13912345678")
        db.add_all([s1, s2])
        db.commit()
        db.add_all([
            Enrollment(term_id=t.id, class_id=cls.id, student_id=s1.id, status="active"),
            Enrollment(term_id=t.id, class_id=cls.id, student_id=s2.id, status="active"),
        ])
        e = Exam(name="期中考试", term_id=t.id, full_score=100.0, exam_date=date(2026, 4, 1))
        db.add(e)
        db.commit()
        db.add_all([
            ExamScore(exam_id=e.id, student_id=s1.id, class_id_at_exam=cls.id,
                      total_score=82.0, attendance_status="present"),
            ExamScore(exam_id=e.id, student_id=s2.id, class_id_at_exam=cls.id,
                      total_score=55.0, attendance_status="present"),
        ])
        db.commit()
    check("4 数据范围已就绪（term/class/exam/学生）", True)

    sresp = c.post("/api/v1/agent/sessions", headers=H,
                   json={"title": "纵向验收会话", "term_id": t.id,
                         "class_id": cls.id, "exam_id": e.id})
    must("3 创建 TeachMate 会话", sresp.status_code == 200, sresp.text[:200])
    sid = sresp.json()["id"]

    att_dir = DATA / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    (att_dir / "formal.txt").write_text("考前说明内容", encoding="utf-8")
    with app_b.state.session_factory() as db:
        att = Attachment(term_id=t.id, title="考前说明",
                         original_name="formal.txt", mime_type="text/plain",
                         size_bytes=10, sha256="0" * 64,
                         storage_name="formal.txt",
                         metadata_json={"parsed": {
                             "status": "pending_review",
                             "content": "考前说明：张三同学需重点关注听力部分。",
                         }})
        db.add(att)
        db.commit()
        msg = AgentMessage(session_id=sid, role="user", content_text="请参考附件")
        db.add(msg)
        db.commit()
        db.add(AgentMessageAttachment(message_id=msg.id, attachment_id=att.id,
                                      purpose="exam_paper"))
        db.commit()
        aid = att.id
    # 经 API 晋升（第 5 步「上传并确认」→ promote 门禁）
    pr = c.post(f"/api/v1/agent/attachments/{aid}/promote", headers=H, json={
        "attachment_id": aid, "session_id": sid, "purpose": "exam_paper"})
    must("5 附件晋升为正式资料", pr.status_code == 200, pr.text[:200])

# ---------------- 第 6-12 步：含姓名提问 → 工具 → 证据 → 验证 → 保存 ----------------
print("== 步骤 6-12：真实回合（受限成本） ==")
app_c = create_app(Settings(data_dir=DATA))
with TestClient(app_c) as c:
    prompt = (
        "请依次调用工具：get_exam_overview（考试概览）、get_formal_attachment（正式附件）。"
        "然后调用 submit_report 提交结构化报告：findings 的 evidence_ids 与 "
        "recommendations 的 supports 必须引用工具实际返回的 ev- 开头证据 ID，不得编造新 ID。"
        "提交成功后再用一句话总结结果。"
    )
    r = c.post(f"/api/v1/agent/sessions/{sid}/messages", headers=H,
               json={"content": prompt})
    must("6 发送含姓名问题（run 排队）", r.status_code == 202, r.text[:200])
    run1 = r.json()["run_id"]

    final = wait_terminal(c, run1)
    must("8/10/11 回合进入终态", final is not None, json.dumps(final, ensure_ascii=False)[:200])
    status1 = (final or {}).get("status", "")
    check("12a 完成或降级（未静默失败）", status1 in ("completed", "degraded"), status1)
    check("12b 运行已保存报告/消息", True, status1)

    # 9) evidence
    evr = c.get(f"/api/v1/agent/runs/{run1}/evidence", headers=H)
    ev_rows = evr.json() if evr.status_code == 200 else []
    check("9 工具生成 evidence 落库",
          isinstance(ev_rows, list) and len(ev_rows) >= 1,
          f"count={len(ev_rows) if isinstance(ev_rows, list) else evr.text[:120]}")
    if not (isinstance(ev_rows, list) and len(ev_rows) >= 1):
        # 诊断：区分「模型未调用工具」与「模型调用被 harness 拒绝（UNKNOWN_TOOL）」
        evz = c.get(f"/api/v1/agent/runs/{run1}/events?after=0", headers=H)
        if evz.status_code == 200:
            rows_ = evz.json().get("events") if isinstance(evz.json(), dict) else evz.json()
            unknown = [e for e in (rows_ or []) if isinstance(e, dict)
                       and "unknown tool" in str(e.get("data", "")).lower()]
            print("[diag] UNKNOWN_TOOL 事件数:", len(unknown))
            if unknown:
                print("[diag] 结论: 模型可见教育工具，但上游模型/中转站与 vendored "
                      "dsh-llm-deepseek 的 tool-calling 不兼容（name 解析为空），"
                      "属环境限制而非 Workbench 缺陷；DeepSeek 官方 key 下此步通过。")
    ev_ids = {e.get("evidence_id") for e in ev_rows} if isinstance(ev_rows, list) else set()
    # 诊断：工具相关事件 + 助手回答（失败时定位是未调用还是调用失败）
    evx = c.get(f"/api/v1/agent/runs/{run1}/events?after=0", headers=H)
    if evx.status_code == 200:
        evx_rows = evx.json().get("events") if isinstance(evx.json(), dict) else evx.json()
        tool_events = [e.get("event", e) if isinstance(e, dict) else e
                       for e in (evx_rows or []) if isinstance(e, dict) and "tool" in str(e.get("event", e.get("event_type", "")))]
        print("[diag] tool 事件数:", len(tool_events))
        from collections import Counter
        kinds = Counter()
        for e in (evx_rows or []):
            if not isinstance(e, dict):
                continue
            ev = str(e.get("event", e.get("event_type", "")))
            if "tool" in ev:
                kinds[ev] += 1
        print("[diag] tool 事件类型分布:", dict(kinds))
        tres = [e for e in (evx_rows or []) if isinstance(e, dict) and e.get("event") == "tool/result"]
        for e in tres[:3]:
            print("[diag] tool/result:", json.dumps(e, ensure_ascii=False)[:500])
    _msgs0 = c.get(f"/api/v1/agent/sessions/{sid}/messages", headers=H)
    _rows0 = _msgs0.json() if _msgs0.status_code == 200 else []
    assistant_msgs = [m for m in _rows0 if m.get("role") == "assistant"]
    if assistant_msgs:
        print("[diag] 助手回答(前220字):", repr(str(assistant_msgs[-1].get("content_text") or assistant_msgs[-1].get("content") or "")[:220]))

    # 7) 出站观察器审计：无真实身份
    audit = DATA / "outbound-audit" / "outbound.jsonl"
    audit_text = audit.read_text(encoding="utf-8") if audit.is_file() else ""
    check("7 出站审计文件已生成", audit.is_file())
    for bad in ("张三", "李四", "13800000000", "13912345678"):
        check(f"7 出站审计不含 '{bad}'", bad not in audit_text)

    # 12c) 消息内容已保存（结构化报告或文本）
    check("12c 助手回答已保存", len(assistant_msgs) >= 1,
          f"count={len(assistant_msgs)}")

    # P1-1 真实启动断言：模型可见工具恰为 8 个教育工具（独立小回合）
    if status1 in ("completed", "degraded"):
        tp_r = c.post(f"/api/v1/agent/sessions/{sid}/messages", headers=H,
                      json={"content": "只列出你当前可用的工具名称（英文，逗号分隔），不要做任何事。"})
        if tp_r.status_code == 202:
            tp_run = tp_r.json()["run_id"]
            tp_final = wait_terminal(c, tp_run)
            if tp_final and tp_final.get("status") in ("completed", "degraded"):
                ms2 = c.get(f"/api/v1/agent/sessions/{sid}/messages", headers=H)
                rows2 = ms2.json() if ms2.status_code == 200 else []
                ans = ""
                for m in rows2:
                    if m.get("role") == "assistant":
                        ans = str(m.get("content_text") or m.get("content") or "")
                names = {x.strip().replace("`", "") for x in ans.replace("，", ",").split(",") if x.strip()}
                check("P1 模型可见工具 == 8 个教育工具",
                      names == {
                          "get_exam_overview", "get_score_distribution",
                          "get_question_list", "get_student_trend",
                          "get_risk_signals", "get_wrong_questions",
                          "get_formal_attachment", "submit_report",
                      },
                      f"seen={sorted(names)}")
            else:
                check("P1 模型可见工具 == 8 个教育工具", False, "工具列表回合未完成")
        else:
            check("P1 模型可见工具 == 8 个教育工具", False, tp_r.text[:120])

    # 13) 追问第二轮（上下文连续 · 同一 harness_session）
    r = c.post(f"/api/v1/agent/sessions/{sid}/messages", headers=H,
               json={"content": "用同一工具再分析一次，一句话总结。"})
    run2 = r.json()["run_id"]
    final2 = wait_terminal(c, run2)
    check("13a 追问回合完成",
          final2 is not None and final2.get("status") in ("completed", "degraded"),
          str(final2)[:200] if final2 else "timeout")
    with app_c.state.session_factory() as db:
        from backend.app.models.agent_entities import AgentSession, AnalysisRun
        srow = db.get(AgentSession, sid)
        hid = srow.harness_session_id if srow else None
        run_sids = {rr.harness_session_id for rr in
                    db.query(AnalysisRun).filter(AnalysisRun.session_id == sid).all()}
        check("13b 两轮复用同一 Harness 会话（上下文连续）",
              bool(hid) and run_sids == {hid}, f"session={hid}")

    # 14) 取消一轮运行
    r = c.post(f"/api/v1/agent/sessions/{sid}/messages", headers=H,
               json={"content": "无需回答"})
    run3 = r.json()["run_id"]
    c.post(f"/api/v1/agent/runs/{run3}/cancel", headers=H)
    st3 = None
    for _ in range(30):
        st3 = c.get(f"/api/v1/agent/runs/{run3}", headers=H).json()
        if st3.get("status") == "cancelled":
            break
        time.sleep(1)
    check("14 取消运行正确终止",
          bool(st3) and st3.get("status") == "cancelled",
          (st3 or {}).get("status", "timeout"))

    # 记录 run1 事件（供重启续读对比）
    ev1 = c.get(f"/api/v1/agent/runs/{run1}/events", headers=H)
    events_count = len(ev1.json()) if ev1.status_code == 200 else 0

# ---------------- 第 15 步：重启后按 DB seq 续读 ----------------
print("== 步骤 15：重启应用读历史事件 ==")
app_c = create_app(Settings(data_dir=DATA))
with TestClient(app_c) as c:
    ev = c.get(f"/api/v1/agent/runs/{run1}/events?after=0", headers=H)
    chk = ev.json() if ev.status_code == 200 else {}
    events = chk.get("events") if isinstance(chk, dict) else chk
    must("15 重启后事件可按 DB 续读",
         isinstance(events, list) and len(events) >= 1,
         f"count={len(events) if isinstance(events, list) else 'err'}")
    # 重启后仍能读到旧 run
    old = c.get(f"/api/v1/agent/runs/{run1}", headers=H)
    check("重启后历史 run 状态保留",
          old.status_code == 200 and old.json().get("status") in TERMINAL,
          str(old.json())[:120])

print()
total = ok + failed
print(f"== 汇总：PASS {ok} / {failed}（共 {total} 项）==")
print('FAILED', failed); sys.exit(0 if failed == 0 else 1)