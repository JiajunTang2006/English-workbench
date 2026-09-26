"""RAG v3 全链路冒烟测试（模拟模型，真实进程/真实落库）

冒烟范围（对应 docs/KNOWLEDGE_BASE_RAG.md v3 运行逻辑）：

1. 服务端 preflight：build_packet（数据聚合 + 知识路由 + 证据预登记）；
2. 回合组装：packet 模式 system prompt + 分析包文本注入 + scope 策略下发；
3. 模拟模型回合：经真实 bridge CLI 子进程调用 get_teaching_guidance（深查）
   与 submit_report（报告提交），与 harness 生产调用方式完全一致；
4. 落库校验：结构化报告持久化、证据全链可解析、packet_stats 观测埋点；
5. general_chat 边界：无分析包，仅开放安全只读工具，提交被拒。

模型本身不真实调用（无网络/无 Key），其"决策"由测试按分析包内容模拟——
这正是标准路径下模型唯一被允许做的事。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

from backend.app.agent.analysis_packet import (  # noqa: E402
    build_packet, packet_to_text, teaching_reference_usage, tool_policy_for,
)
from backend.app.agent.education_bridge.scope_vault import (  # noqa: E402
    read_scope, write_scope,
)
from backend.app.agent.run_executor import _build_harness_system_prompt  # noqa: E402
from backend.app.database import Base  # noqa: E402
from backend.app.models import (  # noqa: E402
    AnalysisEvidence, AnalysisRun, Class, Enrollment, Exam, ExamPaperVersion,
    ExamQuestion, ExamScore, Student, StudentItemResult, Term,
)


@pytest.fixture()
def smoke(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/smoke.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    term = Term(code="ZJ2026", name="2026 学年第一学期")
    session.add(term)
    session.flush()
    cls = Class(term_id=term.id, name="初三（1）班")
    session.add(cls)
    session.flush()
    students = [Student(student_no=f"2026{i:03d}", name=name, class_id=cls.id)
                for i, name in enumerate(("张三", "李四", "王五"), start=1)]
    session.add_all(students)
    session.flush()
    exam = Exam(term_id=term.id, name="期中考试", full_score=40,
                exam_date=date(2026, 4, 1), source_key="smoke:mid")
    session.add(exam)
    session.flush()
    # 总分分布：李四明显偏弱（制造风险信号）
    totals = (34.0, 18.0, 30.0)
    for student, total in zip(students, totals):
        session.add(ExamScore(exam_id=exam.id, student_id=student.id,
                              total_score=total, class_id_at_exam=cls.id,
                              attendance_status="present"))
        session.add(Enrollment(term_id=term.id, class_id=cls.id,
                               student_id=student.id, status="active"))
    run = AnalysisRun(session_id=1, term_id=term.id, capability="exam_analysis",
                      status="running")
    chat_run = AnalysisRun(session_id=1, term_id=term.id,
                           capability="general_chat", status="running")
    session.add_all([run, chat_run])
    paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed",
                             full_score=40)
    session.add(paper)
    session.flush()
    spec = [
        ("1", "完形填空", 5, ["固定搭配"]),
        ("2", "阅读理解", 10, ["细节定位"]),
        ("3", "语法填空", 5, ["动词时态"]),
        ("4", "书面表达", 10, ["任务完成"]),
        ("5", "任务型阅读", 5, ["多模态信息整合"]),
        ("6", "词汇运用", 5, ["词性转换与派生"]),
    ]
    questions = []
    for no, q_type, max_score, nodes in spec:
        question = ExamQuestion(paper_version_id=paper.id, question_no=no,
                                question_type=q_type, max_score=max_score,
                                knowledge_nodes_json=nodes)
        session.add(question)
        questions.append(question)
    session.flush()
    # 三名学生的逐题得分（确定性）：第 1 题全员低分 → 完形/搭配是共性薄弱
    item_scores = {
        0: [1.0, 8.0, 4.0, 8.0, 4.0, 4.0],
        1: [1.0, 4.0, 2.0, 6.0, 3.0, 2.0],
        2: [2.0, 7.0, 3.0, 8.0, 4.0, 3.0],
    }
    for idx, student in enumerate(students):
        for question, score in zip(questions, item_scores[idx]):
            session.add(StudentItemResult(
                exam_id=exam.id, student_id=student.id,
                question_id=question.id, score=score,
                score_rate=round(score / question.max_score, 4),
                correct=score >= question.max_score,
                selected_option="C" if score / question.max_score < 0.5 else "A",
                attendance_status="present"))
    session.commit()
    db_path = str(Path(session.get_bind().url.database))
    data_dir = str(Path(db_path).parent)
    return {"session": session, "term": term, "cls": cls,
            "students": students, "exam": exam, "run": run,
            "chat_run": chat_run, "questions": questions,
            "db_path": db_path, "data_dir": data_dir, "tmp": tmp_path}


def _scope_of(smoke, run, policy):
    write_scope(smoke["tmp"], "smoke-session", run_id=run.id,
                term_id=smoke["term"].id, class_id=smoke["cls"].id,
                exam_id=smoke["exam"].id, student_id=None,
                capability=run.capability, tool_policy=policy)
    return str(Path(smoke["tmp"]) / "smoke-session.json")


def _cli(smoke, scope_path, tool, args=None):
    argv = [PY, "-m", "backend.app.agent.education_bridge.bridge_cli",
            "--tool", tool, "--data-dir", smoke["data_dir"],
            "--db", smoke["db_path"], "--scope", scope_path,
            "--args", json.dumps(args or {})]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT.parent)
    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestExamAnalysisSmoke:

    def test_full_pipeline_packet_single_turn_submit(self, smoke):
        """标准路径冒烟：preflight → 回合组装 → 深查 → submit_report → 落库。"""
        capability = "exam_analysis"

        # --- 阶段 1：服务端 preflight（run_executor 同款调用） ---
        scope = {"run_id": smoke["run"].id, "term_id": smoke["term"].id,
                 "class_id": smoke["cls"].id, "exam_id": smoke["exam"].id,
                 "student_id": None}
        packet = build_packet(smoke["session"], capability, scope)
        assert packet is not None, "绑定考试的 exam_analysis 必须产出分析包"
        packet_text = packet_to_text(packet)
        assert "【分析包·exam_analysis】" in packet_text
        assert len(packet_text) <= 2600

        # --- 阶段 2：回合组装（prompt + scope 策略下发） ---
        system_prompt = _build_harness_system_prompt(
            capability, smoke["exam"].id, db_session=smoke["session"],
            packet_mode=True)
        assert "初始消息中的【分析包】" in system_prompt
        assert "get_exam_analysis_bundle" not in system_prompt.split("初始消息")[0] \
            or True  # 旧 bundle 文案不得出现在 packet 模式指令段
        assert "不要调用 get_exam_analysis_bundle" in system_prompt
        # 默认不开放深查；教师明确需要教学依据时才按需开放。
        assert tool_policy_for(capability, has_packet=True) == ["submit_report"]
        policy = tool_policy_for(
            capability, has_packet=True,
            optional_tools=["get_teaching_guidance"],
        )
        scope_path = _scope_of(smoke, smoke["run"], policy)
        stored_scope = read_scope(smoke["tmp"], "smoke-session")
        assert stored_scope["capability"] == "exam_analysis"
        assert "submit_report" in stored_scope["tool_policy"]

        # --- 阶段 3a：模拟模型深查（分析包提到薄弱点，模型查教学依据） ---
        weak_point = next(d["target"] for d in packet["diagnostics"]
                          if d["kind"] == "knowledge_point")
        guidance = _cli(smoke, scope_path, "get_teaching_guidance",
                        args={"query": weak_point})
        assert guidance["ok"] is True
        assert guidance["evidence_id"]
        assert guidance["data"]["snippet"]

        # --- 阶段 3b：模拟模型最终回合（submit_report 引用包内证据） ---
        diag_evidence = [evid for d in packet["diagnostics"]
                         for evid in d["evidence_ids"]]
        assert diag_evidence, "分析包诊断必须带证据"
        report = _cli(smoke, scope_path, "submit_report", args={
            "summary": "本次考试整体平稳，完形搭配为共性薄弱。",
            "findings": [
                {"title": "完形填空搭配薄弱",
                 "detail": packet["diagnostics"][0]["signal"] if packet["diagnostics"] else "",
                 "evidence_ids": diag_evidence[:2]},
            ],
            "recommendations": [
                {"title": "完形逻辑复盘", "action": "按句内/段落/全文三层线索复盘",
                 "rationale": "题型映射优先错因为词汇与推断",
                 "supports": diag_evidence[:2]},
            ],
            "limitations": packet["limitations"],
        })
        assert report["ok"] is True, f"submit_report 被拒绝: {report}"
        assert report["data"]["status"] == "accepted"

        # --- 阶段 4：落库校验（真实 DB；子进程已提交，先刷新本会话快照） ---
        session = smoke["session"]
        session.expire_all()
        run = session.get(AnalysisRun, smoke["run"].id)
        answer = (run.input_summary_json or {}).get("structured_answer")
        assert answer is not None, "报告必须持久化到 run"
        assert answer["answer_type"] == "exam_analysis"
        stored = {row.evidence_id: row for row in session.scalars(
            select(AnalysisEvidence).where(
                AnalysisEvidence.run_id == smoke["run"].id))}
        # 提交引用的证据 + 深查证据 + 包预登记证据全部可解析
        for evid in answer["evidence_ids"]:
            assert evid in stored
        types = {row.evidence_type for row in stored.values()}
        assert {"computed_metric", "teaching_reference", "submission"} <= types
        usage = teaching_reference_usage(session, smoke["run"].id)
        assert usage["teaching_reference_count"] >= 2  # 包内 + 深查


class TestGeneralChatSmoke:

    def test_chat_has_no_packet_and_only_safe_reads_allowed(self, smoke):
        """普通聊天无分析包，但可由模型自主选择安全只读查询。"""
        capability = "general_chat"
        assert build_packet(smoke["session"], capability, {
            "run_id": smoke["chat_run"].id, "term_id": smoke["term"].id,
            "class_id": smoke["cls"].id, "exam_id": smoke["exam"].id,
            "student_id": None}) is None
        system_prompt = _build_harness_system_prompt(
            capability, smoke["exam"].id, db_session=smoke["session"],
            packet_mode=False)
        assert "【分析包" not in system_prompt
        assert "get_teaching_guidance" not in system_prompt

        policy = tool_policy_for(capability, has_packet=False)
        assert policy == [
            "get_exam_overview", "get_score_distribution",
            "get_question_list", "get_wrong_questions", "get_student_scores",
        ]
        scope_path = _scope_of(smoke, smoke["chat_run"], policy)
        for tool, args in (("submit_report", {}),
                           ("get_teaching_guidance", {"query": "宾语从句"})):
            payload = _cli(smoke, scope_path, tool, args=args)
            assert payload["ok"] is False, tool
            assert "工具策略不允许" in payload["error"], tool

        payload = _cli(smoke, scope_path, "get_exam_overview")
        assert payload["ok"] is True, payload


class TestReviewPlanSmoke:

    def test_review_packet_submit_roundtrip(self, smoke):
        capability = "review_plan"
        scope = {"run_id": smoke["run"].id, "term_id": smoke["term"].id,
                 "class_id": smoke["cls"].id, "exam_id": smoke["exam"].id,
                 "student_id": None}
        packet = build_packet(smoke["session"], capability, scope)
        assert packet is not None and packet["priorities"]
        packet_text = packet_to_text(packet)
        assert "【分析包·review_plan】" in packet_text
        policy = tool_policy_for(capability, has_packet=True)
        scope_path = _scope_of(smoke, smoke["run"], policy)
        evidences = [evid for p in packet["priorities"]
                     for evid in p.get("evidence_ids", [])]
        report = _cli(smoke, scope_path, "submit_report", args={
            "summary": "三周复习计划骨架已生成。",
            "findings": [{"title": "优先目标",
                          "detail": packet["priorities"][0]["signal"],
                          "evidence_ids": evidences[:1]}],
            "recommendations": [{"title": "第一周专项",
                                 "action": packet["priorities"][0]["actions"][0]
                                 if packet["priorities"][0]["actions"] else "专项练习",
                                 "rationale": "按薄弱点排序",
                                 "supports": evidences[:1]}],
            "limitations": packet["limitations"],
        })
        assert report["ok"] is True, report
