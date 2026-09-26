"""浙江中考黄金样例评测（RAG v3 · P4）

确定性黄金断言：不跑真实模型，验证"服务端预计算 + 知识路由"在一份贴近
浙江中考结构的试卷上产出符合预期的诊断、行动、证据与边界。

黄金口径（依据 docs/KNOWLEDGE_BASE_RAG.md 与两份知识源）：
- 阅读理解细节定位、完形搭配、语法时态为薄弱点时必须被诊断命中；
- 每条诊断带数据 evidence；使用知识时带 teaching_reference evidence；
- 听力/人机对话只给跨地区通用诊断并显式标注地区边界；
- 分析包文本规模显著小于 v2 全量数据包（token 效率验收代理指标）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.agent.analysis_packet import (
    build_packet, packet_to_text, teaching_reference_usage,
)
from backend.app.database import Base
from backend.app.models import (
    AnalysisEvidence, AnalysisRun, Class, Enrollment, Exam, ExamPaperVersion,
    ExamQuestion, ExamScore, Student, StudentItemResult, Term,
)

# 黄金试卷结构：题型/满分/知识点/每班平均得分率预期
PAPER_SPEC = [
    {"no": "1", "type": "阅读理解", "max": 10, "nodes": ["细节定位"], "rate": 0.30},
    {"no": "2", "type": "阅读理解", "max": 10, "nodes": ["推断判断"], "rate": 0.80},
    {"no": "3", "type": "完形填空", "max": 5, "nodes": ["固定搭配"], "rate": 0.36},
    {"no": "4", "type": "词汇运用", "max": 5, "nodes": ["词性转换与派生"], "rate": 0.78},
    {"no": "5", "type": "语法填空", "max": 5, "nodes": ["动词时态"], "rate": 0.40},
    {"no": "6", "type": "任务型阅读", "max": 5, "nodes": ["多模态信息整合"], "rate": 0.82},
    {"no": "7", "type": "书面表达", "max": 10, "nodes": ["任务完成"], "rate": 0.70},
    {"no": "8", "type": "听力理解", "max": 5, "nodes": ["listening.gist"], "rate": 0.34},
]


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/golden.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    term = Term(code="ZJ2026", name="2026 学年第一学期")
    session.add(term)
    session.flush()
    classes = [Class(term_id=term.id, name=f"初三（{i}）班") for i in (1, 2)]
    session.add_all(classes)
    session.flush()
    students = []
    for idx in range(9):
        student = Student(student_no=f"2026{idx:03d}", name=f"学生{idx:02d}",
                          class_id=classes[idx % 2].id)
        session.add(student)
        students.append(student)
    session.flush()
    for student in students[:8]:
        session.add(Enrollment(term_id=term.id, class_id=student.class_id,
                               student_id=student.id, status="active"))
    exam = Exam(term_id=term.id, name="2026 年浙江中考模拟", full_score=55,
                source_key="zj:mock")
    session.add(exam)
    session.flush()
    # 学生 0~7 实考；学生 8 缺考（不进分母）
    for idx, student in enumerate(students):
        present = idx < 8
        # 缺考者不写总分；实考者给一个确定总分（低于阈值 60 的 2 人制造风险信号）
        session.add(ExamScore(
            exam_id=exam.id, student_id=student.id,
            total_score=(48.0 + idx * 3.0) if present else None,
            class_id_at_exam=student.class_id,
            attendance_status="present" if present else "absent"))
    run = AnalysisRun(session_id=1, term_id=term.id, capability="exam_analysis",
                      status="running")
    session.add(run)
    # 更早的草稿版本（含干扰题"99"）：验证 confirmed 优先
    draft = ExamPaperVersion(exam_id=exam.id, version=1, status="draft",
                             full_score=55)
    session.add(draft)
    session.flush()
    session.add(ExamQuestion(paper_version_id=draft.id, question_no="99",
                             max_score=10))
    paper = ExamPaperVersion(exam_id=exam.id, version=2, status="confirmed",
                             full_score=55)
    session.add(paper)
    session.flush()
    questions = []
    for spec in PAPER_SPEC:
        question = ExamQuestion(paper_version_id=paper.id, question_no=spec["no"],
                                question_type=spec["type"], max_score=spec["max"],
                                knowledge_nodes_json=spec["nodes"])
        session.add(question)
        questions.append(question)
    session.flush()

    # 逐题得分：按 spec.rate 生成确定性的两班差异（2 班整体更低）
    for idx, student in enumerate(students[:8]):
        weaker_class = student.class_id == classes[1].id
        for spec, question in zip(PAPER_SPEC, questions):
            rate = spec["rate"] - (0.10 if weaker_class else 0.0)
            score = round(spec["max"] * min(rate, 1.0), 1)
            session.add(StudentItemResult(
                exam_id=exam.id, student_id=student.id, question_id=question.id,
                score=score, score_rate=round(score / spec["max"], 4),
                correct=score >= spec["max"], selected_option="C" if rate < 0.5 else "A",
                attendance_status="present"))
    session.commit()
    db_path = str(Path(session.get_bind().url.database))
    return {"session": session, "term": term, "classes": classes,
            "students": students, "exam": exam, "run": run,
            "questions": questions, "db_path": db_path,
            "data_dir": str(Path(db_path).parent)}


def _scope(env, **overrides):
    scope = {"run_id": env["run"].id, "term_id": env["term"].id,
             "class_id": None, "exam_id": env["exam"].id, "student_id": None}
    scope.update(overrides)
    return scope


class TestGoldenExamPacket:

    @pytest.fixture()
    def packet(self, env):
        return build_packet(env["session"], "exam_analysis", _scope(env))

    def test_golden_weak_knowledge_points(self, packet):
        # 契约：知识点诊断取加权得分率最低的 3 条（<0.6）
        points = {d["target"]: d for d in packet["diagnostics"]
                  if d["kind"] == "knowledge_point"}
        assert len(points) == 3
        # 黄金预期：得分率最低的三个知识点（细节定位 0.25 / listening.gist
        # 0.29 / 固定搭配 0.31）必须全部命中
        assert {"细节定位", "listening.gist", "固定搭配"} == set(points)
        # 非薄弱点不得进入诊断（推断判断 0.8、动词时态 0.35 在 3 名之外）
        assert "推断判断" not in points
        # 每条薄弱点必须有行动建议与教学依据证据
        for diagnostic in points.values():
            assert diagnostic["actions"], diagnostic
            assert any(e.startswith("ev-") for e in diagnostic["evidence_ids"])

    def test_golden_weak_questions_with_routing(self, packet):
        # 契约：诊断总数上限 5 —— 知识点占 3 条后，题目按得分率取前 2 条
        questions = {d["target"]: d for d in packet["diagnostics"]
                     if d["kind"] == "question"}
        assert set(questions) == {"第1题", "第8题"}
        reading = questions["第1题"]
        assert reading["error_cause_candidates"] == ["定位", "推断", "审题", "词汇"]
        # 听力题路由只给跨地区通用错因
        listening = questions["第8题"]
        assert set(listening["error_cause_candidates"]) <= {
            "审题", "词汇", "语法", "定位", "推断", "表达"}

    def test_golden_listening_region_boundary(self, packet):
        """听力地区边界：出现听力题时必须显式声明地区限定。"""
        assert any("听力" in text and "地区" in text
                   for text in packet["limitations"])
        blob = json.dumps(packet, ensure_ascii=False)
        for city in ("杭州", "宁波", "温州", "绍兴"):
            assert city not in blob, "听力诊断不得包含具体地市规则"

    def test_golden_confirmed_paper_wins(self, packet):
        # 草稿卷干扰题"99"不得进入诊断（只查语义字段，evidence ID 是随机
        # 十六进制，可能偶然含"99"）。
        for diagnostic in packet["diagnostics"]:
            assert diagnostic["target"] != "第99题"
            assert "第 99 题" not in diagnostic["signal"]
        questions = {q.get("question_no")
                     for q in [{"question_no": d["target"].replace("第", "").replace("题", "")}
                               for d in packet["diagnostics"] if d["kind"] == "question"]}
        assert "99" not in questions

    def test_golden_metrics_and_participants(self, env, packet):
        # 学生 8 缺考 → 参与人数 8
        assert packet["metrics"]["participant_count"] == 8
        assert packet["metrics"]["exam_name"] == "2026 年浙江中考模拟"

    def test_golden_evidence_chain_resolves(self, env, packet):
        rows = env["session"].scalars(select(AnalysisEvidence).where(
            AnalysisEvidence.run_id == env["run"].id)).all()
        stored = {row.evidence_id: row for row in rows}
        referenced = {evid for d in packet["diagnostics"]
                      for evid in d["evidence_ids"]}
        assert referenced, "黄金诊断必须引用证据"
        assert referenced <= set(stored)
        types = {stored[evid].evidence_type for evid in referenced}
        assert "teaching_reference" in types, "使用知识必须登记教学依据证据"
        usage = teaching_reference_usage(env["session"], env["run"].id)
        assert usage["teaching_reference_count"] >= 1

    def test_golden_packet_text_compact_and_smaller_than_v2_bundle(
            self, env, packet):
        """token 效率代理验收：分析包文本 < 2600 字且显著小于 v2 数据包。"""
        text = packet_to_text(packet)
        assert 0 < len(text) <= 2600
        from backend.app.agent.education_bridge.bridge_cli import (
            _Anonymizer, _tool_exam_analysis_bundle,
        )
        from backend.app.agent.privacy import PrivacyMapper
        from backend.app.agent.tools.tool_context import ToolContext, set_tool_context
        anon = _Anonymizer(PrivacyMapper(allow_student_names=True))
        token = set_tool_context(ToolContext(
            db_session=env["session"],
            scope={"exam_id": env["exam"].id, "term_id": env["term"].id}))
        try:
            bundle = _tool_exam_analysis_bundle(env["session"], {
                "exam_id": env["exam"].id, "term_id": env["term"].id,
                "run_id": env["run"].id}, anon, {})
        finally:
            from backend.app.agent.tools.tool_context import reset_tool_context
            reset_tool_context(token)
        bundle_size = len(json.dumps(bundle.get("data", {}), ensure_ascii=False))
        assert len(text) < bundle_size * 0.5, (
            f"分析包 {len(text)} 字应显著小于 v2 数据包 {bundle_size} 字")


class TestGoldenReviewPacket:

    def test_review_priorities_follow_weak_points(self, env):
        packet = build_packet(env["session"], "review_plan", _scope(env))
        assert packet is not None
        targets = [p["target"] for p in packet["priorities"]]
        assert "细节定位" in targets or "固定搭配" in targets
        assert targets[-1] == "计划骨架"
        for priority in packet["priorities"][:-1]:
            assert priority["actions"], "每个优先目标必须有可执行行动"
