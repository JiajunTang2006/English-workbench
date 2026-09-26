"""Bridge 工具策略门与教学深查工具测试（RAG v3 · P2/P3）"""

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


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/bridge.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


from backend.app.database import Base  # noqa: E402
from backend.app.models import (  # noqa: E402
    AnalysisEvidence, AnalysisRun, Class, Enrollment, Exam, ExamScore,
    Student, Term,
)


@pytest.fixture()
def data(db):
    t = Term(code="T1", name="学期")
    db.add(t)
    db.commit()
    c = Class(name="一班", term_id=t.id)
    e = Exam(name="期中考试", term_id=t.id, full_score=100.0,
             exam_date=date(2026, 4, 1))
    db.add_all([c, e])
    db.commit()
    s1 = Student(name="张三", student_no="20260001")
    s2 = Student(name="李四", student_no="20260002")
    db.add_all([s1, s2])
    db.commit()
    db.add_all([
        Enrollment(term_id=t.id, class_id=c.id, student_id=s1.id, status="active"),
        Enrollment(term_id=t.id, class_id=c.id, student_id=s2.id, status="active"),
    ])
    db.add_all([
        ExamScore(exam_id=e.id, student_id=s1.id, class_id_at_exam=c.id,
                  total_score=85.0, attendance_status="present"),
        ExamScore(exam_id=e.id, student_id=s2.id, class_id_at_exam=c.id,
                  total_score=52.0, attendance_status="present"),
    ])
    run = AnalysisRun(session_id=1, term_id=t.id, capability="exam_analysis",
                      status="running")
    chat_run = AnalysisRun(session_id=1, term_id=t.id, capability="general_chat",
                           status="running")
    db.add_all([run, chat_run])
    db.commit()
    db_path = str(Path(db.get_bind().url.database))
    return {"term_id": t.id, "class_id": c.id, "exam_id": e.id,
            "s1": s1.id, "s2": s2.id, "run_id": run.id,
            "chat_run_id": chat_run.id, "db_path": db_path,
            "data_dir": str(Path(db_path).parent)}


def _scope_file(tmp_path, data, *, capability="exam_analysis",
                tool_policy=None) -> str:
    scope = {"run_id": data["run_id"], "term_id": data["term_id"],
             "class_id": data["class_id"], "exam_id": data["exam_id"],
             "student_id": None, "capability": capability,
             "tool_policy": tool_policy}
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({k: v for k, v in scope.items() if v is not None},
                               ensure_ascii=False), encoding="utf-8")
    return str(path)


def _run_cli(db_path, scope_path, tool, args=None, data_dir=None):
    argv = [PY, "-m", "backend.app.agent.education_bridge.bridge_cli",
            "--tool", tool, "--data-dir", data_dir or str(Path(db_path).parent),
            "--db", db_path, "--scope", scope_path,
            "--args", json.dumps(args or {})]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT.parent)
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env)


class TestToolPolicyGate:

    def test_policy_rejects_data_tools_in_packet_mode(self, db, data, tmp_path):
        """分析包模式：数据工具被策略拒绝（模型只见 submit_report 等）。"""
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        proc = _run_cli(data["db_path"], scope, "get_exam_analysis_bundle")
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False
        assert "工具策略不允许" in payload["error"]
        assert "get_exam_analysis_bundle" in payload["error"]

    def test_policy_allows_whitelisted_tools(self, db, data, tmp_path):
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        proc = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                        args={"query": "宾语从句"})
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True, payload

    def test_legacy_scope_without_policy_uses_safe_defaults(self, db, data, tmp_path):
        """缺少 tool_policy 的旧 scope 也不能恢复全量数据工具。"""
        scope = _scope_file(tmp_path, data, tool_policy=None)
        proc = _run_cli(data["db_path"], scope, "get_exam_analysis_bundle")
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False, payload
        assert "工具策略不允许" in payload["error"]

    def test_general_chat_rejects_all_tools(self, db, data, tmp_path):
        """生效边界：普通聊天不开放任何教学工具。"""
        scope = _scope_file(tmp_path, data, capability="general_chat",
                            tool_policy=[])
        for tool in ("get_teaching_guidance", "get_exam_analysis_bundle",
                     "submit_report"):
            proc = _run_cli(data["db_path"], scope, tool,
                            args={"query": "宾语从句"} if tool ==
                            "get_teaching_guidance" else {})
            payload = json.loads(proc.stdout)
            assert payload["ok"] is False, tool
            assert "普通聊天" in payload["error"], tool

    def test_guidance_policy_blocked_when_not_listed(self, db, data, tmp_path):
        scope = _scope_file(tmp_path, data, tool_policy=["submit_report"])
        proc = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                        args={"query": "宾语从句"})
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False
        assert "工具策略不允许" in payload["error"]


class TestTeachingGuidance:

    def test_exact_point_id_lookup(self, db, data, tmp_path):
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        proc = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                        args={"query": "reading.inference"})
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True
        entry = payload["data"]
        assert entry["entry_id"].startswith("CP-")
        assert entry["snippet"], "深查必须返回短片段"
        assert len(entry["snippet"]) <= 800
        assert entry["credibility"] in {"A", "B", "C", "D"}

    def test_lookup_records_teaching_evidence(self, db, data, tmp_path):
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        proc = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                        args={"query": "完形填空"})
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True
        assert payload["evidence_id"], "深查必须登记 teaching_reference 证据"
        session = sessionmaker(bind=create_engine(
            f"sqlite:///{data['db_path']}"))()
        try:
            row = session.scalar(select(AnalysisEvidence).where(
                AnalysisEvidence.evidence_id == payload["evidence_id"]))
            assert row is not None
            assert row.evidence_type == "teaching_reference"
            assert row.run_id == data["run_id"]
        finally:
            session.close()

    def test_unknown_query_fails_with_clear_error(self, db, data, tmp_path):
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        proc = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                        args={"query": "量子力学"})
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False
        assert "知识库未命中" in payload["error"]

    def test_guidance_tool_listed_in_whitelist(self):
        from backend.app.agent.education_bridge.bridge_cli import _ALLOWED_TOOLS
        assert "get_teaching_guidance" in _ALLOWED_TOOLS

    def test_guidance_hard_cap_after_two_successes(self, db, data, tmp_path):
        """回归（评审 P3）：深查两次上限必须是硬门，不能只靠提示词。"""
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        first = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                         args={"query": "阅读理解"})
        assert json.loads(first.stdout)["ok"] is True
        second = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                          args={"query": "完形填空"})
        assert json.loads(second.stdout)["ok"] is True
        third = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                         args={"query": "语法填空"})
        payload = json.loads(third.stdout)
        assert payload["ok"] is False
        assert "上限" in payload["error"]

    def test_packet_teaching_evidence_does_not_consume_cap(self, db, data, tmp_path):
        """分析包预登记的 teaching_reference 证据不占深查额度。"""
        for i in range(4):
            db.add(AnalysisEvidence(
                evidence_id=f"ev-pkt{i}", run_id=data["run_id"],
                evidence_type="teaching_reference",
                local_fact_json={"facts": []}, source_entity=f"entry:pkt{i}",
                display_summary="包预登记", contains_personal_data=False))
        db.commit()
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        first = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                         args={"query": "阅读理解"})
        assert json.loads(first.stdout)["ok"] is True, first.stdout
        second = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                          args={"query": "完形填空"})
        assert json.loads(second.stdout)["ok"] is True, second.stdout

    def test_guidance_failed_lookup_does_not_consume_cap(self, db, data, tmp_path):
        """未命中不计次（错误输出极短，无上下文扩张）。"""
        scope = _scope_file(tmp_path, data,
                            tool_policy=["submit_report", "get_teaching_guidance"])
        miss = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                        args={"query": "不存在的考点"})
        assert json.loads(miss.stdout)["ok"] is False
        hit = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                       args={"query": "阅读理解"})
        assert json.loads(hit.stdout)["ok"] is True
        hit2 = _run_cli(data["db_path"], scope, "get_teaching_guidance",
                        args={"query": "完形填空"})
        assert json.loads(hit2.stdout)["ok"] is True
