"""B2-06 报告证据归属验证测试

覆盖：
- finding evidence_ids 与 recommendation supports 的统一归属契约：
  非空、存在（内存 EvidenceLedger + 当前 run 持久化证据）、归属当前 run_id、
  类型允许、不引用其他运行；
- 重复 ID 规范化去重且不改变展示顺序；
- report_tools、Pydantic 模型、OutputValidator 使用同一份契约来源。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.agent.evidence import (
    EvidenceLedger,
    normalize_evidence_refs,
    validate_report_evidence_references,
)
from backend.app.agent.models import Finding, Recommendation, StructuredAnswer
from backend.app.agent.output_validator import OutputValidator
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.schema_contract import FINDING_SCHEMA, RECOMMENDATION_SCHEMA
from backend.app.agent.tools.report_tools import _submit_teaching_report
from backend.app.agent.tools.tool_context import (
    ToolContext,
    reset_tool_context,
    set_tool_context,
)
from backend.app.database import Base
from backend.app.models.agent_entities import AnalysisEvidence, AnalysisRun


@pytest.fixture
def ledger():
    ledger = EvidenceLedger(run_id=7)
    ledger.add("db_metric", {"pass_rate": 0.8}, source_entity="exam", display_summary="及格率 80%")
    ledger.add("rule_signal", {"risk": "critical"}, source_entity="学生", display_summary="风险信号")
    return ledger


@pytest.fixture
def db_session():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b206_")
    Path(path).unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        run_other = AnalysisRun(
            id=99, session_id=1, term_id=1, capability="exam_analysis",
            status="completed",
        )
        session.add(run_other)
        session.flush()
        session.add(AnalysisEvidence(
            run_id=99, evidence_id="ev_other_run",
            evidence_type="db_metric", local_fact_json={"v": 1},
        ))
        session.commit()
        yield session
    engine.dispose()


def _tool_ctx(ledger, run_id=7, db_session=None):
    return ToolContext(
        scope={"run_id": run_id, "term_id": 1},
        evidence_ledger=ledger,
        db_session=db_session,
    )


# ---------------------------------------------------------------------------
# 共享校验函数（契约本体）
# ---------------------------------------------------------------------------


class TestSharedContract:
    def test_normalize_refs_dedup_preserves_order(self):
        assert normalize_evidence_refs(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]
        assert normalize_evidence_refs([]) == []
        assert normalize_evidence_refs(None) == []

    def test_foreign_ledger_ownership_rejected(self):
        """用 run 99 的 Ledger 校验 run 7 → 必须拒绝（内存账本所有权）。"""
        foreign = EvidenceLedger(run_id=99)
        foreign.add("db_metric", {"v": 1})
        errors = validate_report_evidence_references(
            [{"title": "f", "evidence_ids": ["ev_1"]}],
            [{"action": "r", "supports": ["ev_1"]}],
            ledger=foreign, run_id=7,
        )
        assert errors, "run 99 的 Ledger 校验 run 7 必须失败"
        assert any("其他运行" in e for e in errors)

    def test_own_ledger_ownership_accepted(self, ledger):
        errors = validate_report_evidence_references(
            [{"title": "f", "evidence_ids": ["ev_1"]}],
            [{"action": "r", "supports": ["ev_1"]}],
            ledger=ledger, run_id=7,
        )
        assert errors == []

    def test_valid_refs_pass(self, ledger):
        report = {
            "findings": [{"title": "f1", "evidence_ids": ["ev_1"]}],
            "recommendations": [{"action": "r1", "supports": ["ev_1", "ev_2"]}],
        }
        errors = validate_report_evidence_references(
            report["findings"], report["recommendations"], ledger=ledger,
        )
        assert errors == []

    def test_missing_evidence_id_rejected(self, ledger):
        errors = validate_report_evidence_references(
            [{"title": "f1", "evidence_ids": ["ev_999"]}],
            [],
            ledger=ledger,
        )
        assert any("不存在的证据" in e for e in errors)

    def test_missing_supports_rejected(self, ledger):
        errors = validate_report_evidence_references(
            [],
            [{"action": "r1", "supports": ["ev_999"]}],
            ledger=ledger,
        )
        assert any("不存在的证据" in e for e in errors)

    def test_duplicate_refs_are_deduped_but_order_preserved(self, ledger):
        report = {
            "findings": [{"title": "f1", "evidence_ids": ["ev_1", "ev_1"]}],
            "recommendations": [{"action": "r1", "supports": ["ev_2", "ev_2", "ev_1"]}],
        }
        errors = validate_report_evidence_references(
            report["findings"], report["recommendations"], ledger=ledger,
        )
        assert errors == []
        # 展示顺序不因去重改变（display 序列 = 原始提交顺序）
        assert [f["evidence_ids"] for f in report["findings"]] == [["ev_1", "ev_1"]]

    def test_type_whitelist_enforced(self, ledger):
        ledger.add("secret_type", {"x": 1}, source_entity="plug")
        errors = validate_report_evidence_references(
            [{"title": "f1", "evidence_ids": ["ev_3"]}],
            [], ledger=ledger,
        )
        assert any("类型" in e for e in errors)

    def test_db_evidence_belongs_to_other_run_rejected(self, db_session):
        errors = validate_report_evidence_references(
            [{"title": "f1", "evidence_ids": ["ev_other_run"]}],
            [],
            ledger=EvidenceLedger(run_id=7),
            db_session=db_session,
            run_id=7,
        )
        assert any("其他运行" in e for e in errors)

    def test_db_evidence_of_current_run_accepted(self, db_session):
        db_session.add(AnalysisEvidence(
            run_id=7, evidence_id="ev_7", evidence_type="db_metric",
            local_fact_json={"v": 1},
        ))
        db_session.commit()
        errors = validate_report_evidence_references(
            [{"title": "f1", "evidence_ids": ["ev_7"]}],
            [],
            ledger=EvidenceLedger(run_id=7),
            db_session=db_session,
            run_id=7,
        )
        assert errors == []


# ---------------------------------------------------------------------------
# report_tools 工具层（含 recommendation supports 归属校验）
# ---------------------------------------------------------------------------


def _call(**report_kwargs):
    ctx_token = set_tool_context(ToolContext(scope={}, evidence_ledger=None))
    try:
        return _submit_teaching_report(**report_kwargs)
    finally:
        reset_tool_context(ctx_token)


def _call_with_ctx(ledger, **report_kwargs):
    ctx_token = set_tool_context(_tool_ctx(ledger, run_id=7))
    try:
        return _submit_teaching_report(**report_kwargs)
    finally:
        reset_tool_context(ctx_token)


class TestReportToolOwnership:
    def test_valid_evidence_and_supports_accepted(self, ledger):
        result = _call_with_ctx(
            ledger,
            answer_type="exam_analysis",
            summary="总结",
            findings=[{"title": "f1", "evidence_ids": ["ev_1"]}],
            recommendations=[{"action": "r1", "supports": ["ev_2"]}],
            limitations=[],
            scope_snapshot={},
            schema_version="1.0.0",
        )
        assert result["data"]["status"] == "accepted"

    def test_finding_unknown_evidence_rejected(self, ledger):
        result = _call_with_ctx(
            ledger,
            answer_type="exam_analysis", summary="总结",
            findings=[{"title": "f1", "evidence_ids": ["ev_unknown"]}],
            recommendations=[{"action": "r1", "supports": ["ev_1"]}],
            limitations=[], scope_snapshot={}, schema_version="1.0.0",
        )
        assert "error" in result
        assert "证据引用校验失败" in result["error"]

    def test_recommendation_unknown_supports_rejected(self, ledger):
        result = _call_with_ctx(
            ledger,
            answer_type="exam_analysis", summary="总结",
            findings=[{"title": "f1", "evidence_ids": ["ev_1"]}],
            recommendations=[{"action": "r1", "supports": ["ev_unknown"]}],
            limitations=[], scope_snapshot={}, schema_version="1.0.0",
        )
        assert "error" in result
        assert "supports" in result["error"] or "证据引用校验失败" in result["error"]

    def test_cross_run_evidence_rejected_at_tool_layer(self, db_session, ledger):
        ctx_token = set_tool_context(_tool_ctx(ledger, run_id=7, db_session=db_session))
        try:
            result = _submit_teaching_report(
                answer_type="exam_analysis", summary="总结",
                findings=[{"title": "f1", "evidence_ids": ["ev_other_run"]}],
                recommendations=[{"action": "r1", "supports": ["ev_1"]}],
                limitations=[], scope_snapshot={}, schema_version="1.0.0",
            )
        finally:
            reset_tool_context(ctx_token)
        assert "error" in result

    def test_duplicate_refs_still_accepted(self, ledger):
        result = _call_with_ctx(
            ledger,
            answer_type="exam_analysis", summary="总结",
            findings=[{"title": "f1", "evidence_ids": ["ev_1", "ev_1"]}],
            recommendations=[{"action": "r1", "supports": ["ev_2", "ev_2"]}],
            limitations=[], scope_snapshot={}, schema_version="1.0.0",
        )
        assert result["data"]["status"] == "accepted"

    def test_missing_required_refs_rejected(self, ledger):
        result = _call_with_ctx(
            ledger,
            answer_type="exam_analysis", summary="总结",
            findings=[{"title": "f1", "evidence_ids": []}],
            recommendations=[{"action": "r1", "supports": []}],
            limitations=[], scope_snapshot={}, schema_version="1.0.0",
        )
        assert "error" in result

    def test_run_id_required(self):
        result = _call(
            answer_type="exam_analysis", summary="总结",
            findings=[{"title": "f1", "evidence_ids": ["ev_1"]}],
            recommendations=[{"action": "r1", "supports": ["ev_1"]}],
            limitations=[], scope_snapshot={}, schema_version="1.0.0",
        )
        assert "作用域缺少 run_id" in result.get("error", "")


# ---------------------------------------------------------------------------
# OutputValidator 层（含 DB 持久化证据归属）
# ---------------------------------------------------------------------------


class TestValidatorOwnership:
    def _validate(self, ledger, answer, db_session=None, run_id=7):
        validator = OutputValidator()
        return validator.validate(
            structured_answer=answer,
            evidence_ledger=ledger,
            privacy_mapper=PrivacyMapper(),
            raw_text="",
            finish_reason="stop",
            db_session=db_session,
            run_id=run_id,
        )

    def test_valid_report_passes(self, ledger):
        result = self._validate(ledger, {
            "answer_type": "exam_analysis",
            "summary": "总",
            "findings": [{"title": "f1", "evidence_ids": ["ev_1"]}],
            "recommendations": [{"action": "r1", "supports": ["ev_2"]}],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert result.valid

    def test_finding_unknown_evidence_fails(self, ledger):
        result = self._validate(ledger, {
            "answer_type": "exam_analysis",
            "summary": "总",
            "findings": [{"title": "f1", "evidence_ids": ["ev_unknown"]}],
            "recommendations": [{"action": "r1", "supports": ["ev_1"]}],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert not result.valid
        assert any("不存在的证据" in e for e in result.errors)

    def test_recommendation_unknown_supports_fails(self, ledger):
        result = self._validate(ledger, {
            "answer_type": "exam_analysis",
            "summary": "总",
            "findings": [{"title": "f1", "evidence_ids": ["ev_1"]}],
            "recommendations": [{"action": "r1", "supports": ["ev_unknown"]}],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert not result.valid
        assert any("不存在的证据" in e for e in result.errors)

    def test_cross_run_evidence_fails_with_db(self, ledger, db_session):
        result = self._validate(ledger, {
            "answer_type": "exam_analysis",
            "summary": "总",
            "findings": [{"title": "f1", "evidence_ids": ["ev_other_run"]}],
            "recommendations": [{"action": "r1", "supports": ["ev_1"]}],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        }, db_session=db_session, run_id=7)
        assert not result.valid
        assert any("其他运行" in e for e in result.errors)

    def test_duplicate_refs_still_valid(self, ledger):
        result = self._validate(ledger, {
            "answer_type": "exam_analysis",
            "summary": "总",
            "findings": [{"title": "f1", "evidence_ids": ["ev_1", "ev_1"]}],
            "recommendations": [{"action": "r1", "supports": ["ev_2"]}],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert result.valid


# ---------------------------------------------------------------------------
# 工具 Schema、Pydantic 模型与 OutputValidator 契约一致性
# ---------------------------------------------------------------------------


class TestContractConsistency:
    def test_pydantic_and_contract_share_required_refs(self):
        finding_schema = TypeAdapter(Finding).json_schema()
        assert "evidence_ids" in finding_schema["required"]
        assert finding_schema["properties"]["evidence_ids"]["minItems"] >= 1
        assert FINDING_SCHEMA["required"] == ["title", "evidence_ids"]

        rec_schema = TypeAdapter(Recommendation).json_schema()
        assert "supports" in rec_schema["required"]
        assert rec_schema["properties"]["supports"]["minItems"] >= 1
        assert RECOMMENDATION_SCHEMA["required"] == ["action", "supports"]

    def test_pydantic_refuses_empty_refs(self):
        with pytest.raises(ValidationError):
            Finding(title="x", evidence_ids=[])
        with pytest.raises(ValidationError):
            Recommendation(action="x", supports=[])

    def test_structured_answer_uses_contract(self):
        answer = StructuredAnswer(
            answer_type="exam_analysis",
            summary="总",
            findings=[Finding(title="f1", evidence_ids=["e1"])],
            recommendations=[Recommendation(action="r1", supports=["e1"])],
        )
        assert answer.findings[0].evidence_ids == ["e1"]
        assert answer.recommendations[0].supports == ["e1"]

    def test_report_tool_schema_refs_are_shared_objects(self):
        from backend.app.agent.tools import report_tools as rt
        params = rt._REPORT_PARAMETERS_SCHEMA
        assert params["properties"]["findings"]["items"] is FINDING_SCHEMA
        assert params["properties"]["recommendations"]["items"] is RECOMMENDATION_SCHEMA

    def test_tool_and_validator_reject_same_bad_ref(self, ledger):
        """同一伪造 ID 在工具层与最终验证器都被拒绝（同一契约）。"""
        tool_result = _call_with_ctx(
            ledger,
            answer_type="exam_analysis", summary="总结",
            findings=[{"title": "f1", "evidence_ids": ["ev_bad"]}],
            recommendations=[{"action": "r1", "supports": ["ev_1"]}],
            limitations=[], scope_snapshot={}, schema_version="1.0.0",
        )
        assert "error" in tool_result

        validator = OutputValidator()
        result = validator.validate(
            structured_answer={
                "answer_type": "exam_analysis",
                "summary": "总",
                "findings": [{"title": "f1", "evidence_ids": ["ev_bad"]}],
                "recommendations": [{"action": "r1", "supports": ["ev_1"]}],
                "limitations": [],
                "scope_snapshot": {},
                "schema_version": "1.0.0",
            },
            evidence_ledger=ledger,
            privacy_mapper=PrivacyMapper(),
        )
        assert not result.valid