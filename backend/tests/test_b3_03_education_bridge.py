"""B3-03 Education Bridge 测试

覆盖：
1. scope 保管库：原子写入/读取/清理、白名单字段（fail-closed 丢弃未知键）；
2. bridge_cli 全链路（真实子进程调用）：
   - get_exam_overview 成功：facts + evidence 落库 + 返回 evidence_id；
   - 输出脱敏：get_risk_signals 的学生姓名被替换为匿名编号，电话/学号不出现；
   - 模型参数携带 scope 键 → 拒绝（fail-closed）；
   - 参数越权（非白名单键）→ 拒绝；
   - submit_report 引用不存在的 evidence → 拒绝；
   - submit_report 引用其他 run 的 evidence（DB 归属校验）→ 拒绝；
3. TeachMate 专用 cordis 配置：
   - 不含 bash / fs / subagent / tool-web / tool-cordis / 插件安装；
   - 含 education-bridge 白名单插件，且 scopeRoot 由服务器注入；
4. Harness 插件 ESM 可加载；工具清单为教学白名单。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.entities import (
    Term, Class, Exam, Student, Enrollment, ExamScore, Attachment,
)
from backend.app.models.agent_entities import (
    AnalysisRun, AnalysisEvidence, ExamPaperVersion, ExamQuestion,
    StudentItemResult,
)
from backend.app.agent.education_bridge import scope_vault

BACKEND = Path(__file__).resolve().parents[1]
PY = sys.executable


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workbench.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def data(db):
    """最小教学数据：学期/班级/考试/学生/成绩/运行。"""
    t = Term(code="t1", name="2026春", starts_on=date(2026, 1, 1),
             ends_on=date(2026, 7, 1), status="active")
    db.add(t)
    db.commit()
    c = Class(name="一班", term_id=t.id)
    e = Exam(name="期中考试", term_id=t.id, full_score=100.0, exam_date=date(2026, 4, 1))
    db.add_all([c, e])
    db.commit()
    s1 = Student(name="张三", student_no="20260001", parent_phone="13800000000")
    s2 = Student(name="李四", student_no="20260002", parent_phone="13912345678")
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
    db.add(run)
    db.commit()
    return {
        "term_id": t.id, "class_id": c.id, "exam_id": e.id,
        "s1": s1.id, "s2": s2.id, "run_id": run.id,
    }


def _scope_file(tmp_path, data, **overrides) -> str:
    """写服务器视角的 scope 文件（只有白名单字段）。"""
    scope = {"run_id": data["run_id"], "term_id": data["term_id"],
             "class_id": data["class_id"], "exam_id": data["exam_id"],
             "student_id": None}
    scope.update(overrides)
    p = tmp_path / "scope.json"
    p.write_text(json.dumps({k: v for k, v in scope.items() if v is not None},
                            ensure_ascii=False), encoding="utf-8")
    return str(p)


def _run_cli(tool, db_path, scope_path, args=None, data_dir=None):
    argv = [PY, "-m", "backend.app.agent.education_bridge.bridge_cli",
            "--tool", tool, "--data-dir", data_dir or str(Path(db_path).parent),
            "--db", db_path, "--scope", scope_path, "--args", json.dumps(args or {})]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND.parent)
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          timeout=30, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# 1. scope_vault
# ---------------------------------------------------------------------------

class TestScopeVault:

    def test_write_read_clear_roundtrip(self, tmp_path, data):
        hid = "tm-1-abc"
        scope_vault.write_scope(tmp_path, hid, run_id=data["run_id"],
                                term_id=data["term_id"], exam_id=data["exam_id"])
        got = scope_vault.read_scope(tmp_path, hid)
        assert got["run_id"] == data["run_id"]
        assert got["exam_id"] == data["exam_id"]
        scope_vault.clear_scope(tmp_path, hid)
        assert scope_vault.read_scope(tmp_path, hid) is None

    def test_unknown_keys_fail_closed(self, tmp_path):
        scope = {"run_id": 1, "evil": "x", "cmd": "rm -rf /"}
        cleaned = scope_vault.allowlisted_scope(scope)
        assert cleaned == {"run_id": 1, "term_id": None, "class_id": None,
                           "exam_id": None, "student_id": None}
        assert "cmd" not in cleaned

    def test_missing_file_returns_none(self, tmp_path):
        assert scope_vault.read_scope(tmp_path, "tm-999") is None


# ---------------------------------------------------------------------------
# 2. bridge_cli 全链路（真实子进程）
# ---------------------------------------------------------------------------

class TestBridgeCli:

    def test_student_scores_bound_to_server_scope(self, db, data, tmp_path):
        paper = ExamPaperVersion(exam_id=data["exam_id"], version=1,
                                 status="confirmed", full_score=10)
        db.add(paper)
        db.flush()
        question = ExamQuestion(paper_version_id=paper.id, question_no="1",
                                section_name="阅读", max_score=10)
        db.add(question)
        db.flush()
        db.add(StudentItemResult(exam_id=data["exam_id"], student_id=data["s1"],
                                 question_id=question.id, score=8))
        db.commit()

        scope = _scope_file(tmp_path, data, student_id=data["s1"],
                            capability="general_chat")
        out = _run_cli("get_student_scores", db.bind.url.database, scope,
                       args={"question_no": "1"})
        assert out["ok"] is True
        assert out["data"]["data"]["item_scores"][0]["score"] == 8
        assert out["data"]["data"]["detail_status"] == "complete"
        assert any("8/10" in fact["text"] for fact in out["facts"])
        assert "张三" not in json.dumps(out, ensure_ascii=False)
        denied = _run_cli("get_student_scores", db.bind.url.database, scope,
                          args={"student_id": data["s2"]})
        assert denied["ok"] is False


    def test_exam_analysis_bundle_returns_one_evidence_with_core_metrics(
        self, db, data, tmp_path,
    ):
        out = _run_cli(
            "get_exam_analysis_bundle", db.bind.url.database,
            _scope_file(tmp_path, data),
        )
        assert out["ok"] is True
        assert out["evidence_id"]
        bundle = out["data"]["data"]
        assert bundle["overview"]["participant_count"] == 2
        assert "distribution" in bundle["score_distribution"]
        assert "series" in bundle["trend"]
        rows = db.scalars(select(AnalysisEvidence).where(
            AnalysisEvidence.run_id == data["run_id"],
        )).all()
        assert len(rows) == 1
        assert rows[0].evidence_type == "computed_metric"

    def test_exam_overview_success_and_evidence(self, db, data, tmp_path):
        scope = _scope_file(tmp_path, data)
        out = _run_cli("get_exam_overview", db.bind.url.database, scope)
        assert out["ok"] is True
        assert out["evidence_id"] is not None
        assert out["data"]["data"]["participant_count"] == 2
        # evidence 已落库且归属 run
        ev = db.scalar(select(AnalysisEvidence).where(
            AnalysisEvidence.evidence_id == out["evidence_id"]))
        assert ev is not None and ev.run_id == data["run_id"]

    def test_risk_signals_anonymized(self, db, data, tmp_path):
        scope = _scope_file(tmp_path, data)
        out = _run_cli("get_risk_signals", db.bind.url.database, scope, args={"threshold": 60})
        assert out["ok"] is True
        # 输出必须匿名：真实姓名、学号、电话不出现
        blob = json.dumps(out, ensure_ascii=False)
        assert "张三" not in blob
        assert "20260001" not in blob
        assert "13800000000" not in blob
        # 桥接层与主 Agent 统一使用同一套运行级匿名编号。
        assert "student_" in blob
        assert "匿名编号: student_" in blob

    def test_scope_key_in_args_rejected(self, db, data, tmp_path):
        out = _run_cli("get_exam_overview", db.bind.url.database,
                       _scope_file(tmp_path, data), args={"exam_id": 999})
        assert out["ok"] is False
        assert "scope" in out["error"]

    def test_forbidden_arg_key_rejected(self, db, data, tmp_path):
        out = _run_cli("get_exam_overview", db.bind.url.database,
                       _scope_file(tmp_path, data), args={"shell": "id"})
        assert out["ok"] is False
        assert "不允许参数" in out["error"]

    def test_submit_report_unknown_evidence_rejected(self, db, data, tmp_path):
        args = {"findings": [{"title": "x", "evidence_ids": ["ev-nope"]}],
                "recommendations": []}
        out = _run_cli("submit_report", db.bind.url.database,
                       _scope_file(tmp_path, data), args=args)
        assert out["ok"] is False
        assert "证据" in out["error"]

    def test_submit_report_cross_run_evidence_rejected(self, db, data, tmp_path):
        """引用其它 run 的证据失败（DB 归属校验）。"""
        db.add(AnalysisEvidence(evidence_id="ev-other", run_id=999,
                                evidence_type="db_metric"))
        db.commit()
        args = {"findings": [{"evidence_ids": ["ev-other"]}], "recommendations": []}
        out = _run_cli("submit_report", db.bind.url.database,
                       _scope_file(tmp_path, data), args=args)
        assert out["ok"] is False
        assert "run" in out["error"] or "证据" in out["error"]

    def test_submit_report_valid_succeeds(self, db, data, tmp_path):
        # 先产生合法 evidence
        first = _run_cli("get_exam_overview", db.bind.url.database,
                         _scope_file(tmp_path, data))
        assert first["ok"] is True
        args = {"findings": [{"title": "合格率", "evidence_ids": [first["evidence_id"]]}],
                "recommendations": []}
        out = _run_cli("submit_report", db.bind.url.database,
                       _scope_file(tmp_path, data), args=args)
        assert out["ok"] is True
        assert out["data"]["status"] == "accepted"

    def test_submit_report_supports_valid(self, db, data, tmp_path):
        """recommendations 按契约用 supports 引用本回合证据（B3-03 契约对齐）。"""
        first = _run_cli("get_exam_overview", db.bind.url.database,
                         _scope_file(tmp_path, data))
        args = {
            "findings": [{"title": "合格率", "evidence_ids": [first["evidence_id"]]}],
            "recommendations": [
                {"title": "分层辅导", "action": "分组补差",
                 "supports": [first["evidence_id"]]},
            ],
        }
        out = _run_cli("submit_report", db.bind.url.database,
                       _scope_file(tmp_path, data), args=args)
        assert out["ok"] is True
        assert out["data"]["status"] == "accepted"

    def test_submit_report_evidence_ids_compat(self, db, data, tmp_path):
        """recommendations 误用 evidence_ids 时兼容接受（引用仍过严格校验）。"""
        first = _run_cli("get_exam_overview", db.bind.url.database,
                         _scope_file(tmp_path, data))
        args = {
            "findings": [{"title": "合格率", "evidence_ids": [first["evidence_id"]]}],
            "recommendations": [
                {"title": "分层辅导", "evidence_ids": [first["evidence_id"]]},
            ],
        }
        out = _run_cli("submit_report", db.bind.url.database,
                       _scope_file(tmp_path, data), args=args)
        assert out["ok"] is True

    def test_error_output_matches_plugin_schema(self, db, data, tmp_path):
        """错误输出必须满足插件 output.schema 的 required ['ok','data']，
        否则 harness 会把真实错误掩盖成 'invalid output: missing data'。"""
        out = _run_cli("get_exam_overview", db.bind.url.database,
                       _scope_file(tmp_path, data), args={"exam_id": 999})
        assert out["ok"] is False
        assert out["data"] == {}          # 与插件 schema required 对齐
        assert "scope" in out["error"]


def _scope_file(tmp_path, data, **kw):
    p = tmp_path / "scope.json"
    scope = {"run_id": data["run_id"], "term_id": data["term_id"],
             "class_id": data["class_id"], "exam_id": data["exam_id"],
             "student_id": None}
    scope.update(kw)
    p.write_text(json.dumps({k: v for k, v in scope.items() if v is not None}),
                 encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 3. TeachMate cordis 配置
# ---------------------------------------------------------------------------

_CORDIS_ARCHIVE = BACKEND.parent / "backend" / "assets" / "cordis" / "teachmate.cordis.yml"


def _rendered_cordis() -> str:
    """使用与生产一致的运行时生成器产物（实际生效配置）。"""
    from backend.app.agent.runtime.harness_runtime import _render_teachmate_cordis
    return _render_teachmate_cordis()


def test_teachmate_cordis_no_forbidden_plugins():
    text = _rendered_cordis()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # 注释中的说明性提及不算配置
        for bad in ("dsh-tool-bash", "dsh-tool-fs", "dsh-tool-subagent",
                    "dsh-tool-web", "dsh-web", "dsh-tool-cordis",
                    "dsh-shell", "code-runtime"):
            assert bad not in stripped, f"TeachMate cordis 配置行不应包含 {bad}: {stripped}"
    assert "toolBash: false" in text
    assert "education-bridge" in text
    assert "skills:" in text and "enabled: false" in text


def test_teachmate_cordis_requires_plugin_env():
    text = _rendered_cordis()
    assert "DSH_EDUCATION_SCOPE_ROOT" in text
    assert "pythonBin" in text
    # Teaching 白名单插件与 scoped agents 明确注入 tools 服务
    assert "inject:" in text and "- tools" in text


# ---------------------------------------------------------------------------
# 4. 插件 ESM 加载与白名单
# ---------------------------------------------------------------------------

_PLUGIN = BACKEND / "app" / "agent" / "education_bridge" / "harness_plugin" / "index.mjs"


def test_plugin_loads_and_lists_whitelist():
    if shutil.which("node") is None:
        pytest.skip("node 不可用")
    code = (
        "import('file://" + str(_PLUGIN) + "').then(m => {"
        " const ok = typeof m.apply === 'function' && m.default?.name === 'education-bridge';"
        " process.stdout.write(ok ? 'OK' : 'BAD'); }).catch(e => {"
        " process.stdout.write('FAIL:' + e.message); process.exit(1); })"
    )
    proc = subprocess.run([shutil.which("node"), "--input-type=module", "-e", code],
                          capture_output=True, text=True, timeout=30)
    assert "FAIL" not in proc.stdout
    assert "OK" in proc.stdout


def test_plugin_submit_report_schema_guides_supports():
    """submit_report 的 recommendations schema 必须引导 supports（契约对齐）：
    required 含 supports、字段类型为 string 数组；否则模型无法知道要提供
    证据引用，必然在校验器被拒。"""
    if shutil.which("node") is None:
        pytest.skip("node 不可用")
    code = (
        "import('file://" + str(_PLUGIN) + "').then(m => {"
        " const defs = m.TOOL_DEFS;"
        " const sr = defs.find(d => d.name === 'submit_report');"
        " const rec = sr.parameters.properties.recommendations.items;"
        " const ok = !!(rec.required && rec.required.includes('supports')"
        "   && rec.properties && rec.properties.supports"
        "   && rec.properties.supports.type === 'array');"
        " process.stdout.write(ok ? 'OK' : 'BAD:' + JSON.stringify(rec));"
        "}).catch(e => { process.stdout.write('FAIL:' + e.message); process.exit(1); })"
    )
    proc = subprocess.run([shutil.which("node"), "--input-type=module", "-e", code],
                          capture_output=True, text=True, timeout=30)
    assert "OK" in proc.stdout, proc.stdout
