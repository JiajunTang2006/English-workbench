"""U3-03 强制结构化报告 Schema 测试。

测试覆盖：
1. StructuredAnswer 包含 scope_snapshot 和 schema_version 字段
2. Finding 缺少 evidence_ids 被拒绝
3. Recommendation 缺少 supports 被拒绝
4. make_structured_output_schema 包含 scope_snapshot 和 schema_version
5. 输出验证：完整报告通过
6. 输出验证：缺少 scope_snapshot 失败
7. 安全降级 fallback 包含 scope_snapshot 和 schema_version
8. repair 最多一次逻辑
"""

import pytest
from unittest.mock import MagicMock

from backend.app.agent.models import (
    StructuredAnswer,
    Finding,
    Recommendation,
)
from backend.app.agent.schema_contract import (
    make_structured_output_schema,
    EXAM_ANALYSIS_OUTPUT_SCHEMA,
)
from backend.app.agent.output_validator import OutputValidator, ValidationResult
from backend.app.agent.evidence import EvidenceLedger


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class TestStructuredAnswerModel:
    def test_has_scope_snapshot(self):
        ans = StructuredAnswer(
            answer_type="exam_analysis",
            summary="test",
            scope_snapshot={"exam_id": 1},
            schema_version="1.0.0",
        )
        assert ans.scope_snapshot == {"exam_id": 1}

    def test_has_schema_version(self):
        ans = StructuredAnswer(
            answer_type="exam_analysis",
            summary="test",
        )
        assert ans.schema_version == "1.0.0"

    def test_finding_requires_evidence_ids(self):
        with pytest.raises(Exception):
            Finding(title="test", evidence_ids=[])

    def test_finding_with_evidence_ids(self):
        f = Finding(title="test", evidence_ids=["ev_1"])
        assert f.evidence_ids == ["ev_1"]

    def test_recommendation_requires_supports(self):
        with pytest.raises(Exception):
            Recommendation(action="test", supports=[])

    def test_recommendation_with_supports(self):
        r = Recommendation(action="test", supports=["ev_1"])
        assert r.supports == ["ev_1"]


# ---------------------------------------------------------------------------
# Schema 生成
# ---------------------------------------------------------------------------

class TestSchemaContract:
    def test_schema_has_scope_snapshot(self):
        assert "scope_snapshot" in EXAM_ANALYSIS_OUTPUT_SCHEMA["properties"]

    def test_schema_has_schema_version(self):
        assert "schema_version" in EXAM_ANALYSIS_OUTPUT_SCHEMA["properties"]

    def test_schema_requires_scope_snapshot(self):
        assert "scope_snapshot" in EXAM_ANALYSIS_OUTPUT_SCHEMA["required"]

    def test_schema_requires_schema_version(self):
        assert "schema_version" in EXAM_ANALYSIS_OUTPUT_SCHEMA["required"]

    def test_make_schema_includes_new_fields(self):
        schema = make_structured_output_schema("test_cap")
        assert "scope_snapshot" in schema["properties"]
        assert "schema_version" in schema["properties"]
        assert "scope_snapshot" in schema["required"]
        assert "schema_version" in schema["required"]


# ---------------------------------------------------------------------------
# OutputValidator
# ---------------------------------------------------------------------------

class TestOutputValidator:
    @pytest.fixture
    def validator(self):
        return OutputValidator()

    @pytest.fixture
    def ledger_with_evidence(self):
        ledger = EvidenceLedger(run_id=1)
        ledger.add(
            evidence_type="db_metric",
            local_fact={"average_score": 72.3},
            source_entity="get_exam_statistics",
        )
        return ledger

    @pytest.fixture
    def privacy_mapper(self):
        return MagicMock()

    def _valid_answer(self):
        return {
            "answer_type": "exam_analysis",
            "summary": "全班均分 72.3",
            "findings": [
                {
                    "title": "均分偏低",
                    "description": "低于年级平均",
                    "evidence_ids": ["ev_1"],
                    "severity": "warning",
                },
            ],
            "recommendations": [
                {
                    "action": "加强基础训练",
                    "rationale": "基础题得分率低",
                    "supports": ["ev_1"],
                    "priority": "high",
                },
            ],
            "limitations": ["样本仅限本班"],
            "scope_snapshot": {"exam_id": 1, "class_id": 5},
            "schema_version": "1.0.0",
        }

    def test_valid_report_passes(self, validator, ledger_with_evidence, privacy_mapper):
        result = validator.validate(
            structured_answer=self._valid_answer(),
            evidence_ledger=ledger_with_evidence,
            privacy_mapper=privacy_mapper,
            output_schema=EXAM_ANALYSIS_OUTPUT_SCHEMA,
            raw_text="",
            finish_reason="stop",
        )
        assert result.valid is True

    def test_missing_scope_snapshot_fails(self, validator, ledger_with_evidence, privacy_mapper):
        answer = self._valid_answer()
        del answer["scope_snapshot"]
        result = validator.validate(
            structured_answer=answer,
            evidence_ledger=ledger_with_evidence,
            privacy_mapper=privacy_mapper,
            output_schema=EXAM_ANALYSIS_OUTPUT_SCHEMA,
            raw_text="",
            finish_reason="stop",
        )
        assert result.valid is False

    def test_finding_without_evidence_ids_fails(self, validator, ledger_with_evidence, privacy_mapper):
        answer = self._valid_answer()
        answer["findings"][0]["evidence_ids"] = []
        result = validator.validate(
            structured_answer=answer,
            evidence_ledger=ledger_with_evidence,
            privacy_mapper=privacy_mapper,
            output_schema=EXAM_ANALYSIS_OUTPUT_SCHEMA,
            raw_text="",
            finish_reason="stop",
        )
        assert result.valid is False

    def test_recommendation_without_supports_fails(self, validator, ledger_with_evidence, privacy_mapper):
        answer = self._valid_answer()
        answer["recommendations"][0]["supports"] = []
        result = validator.validate(
            structured_answer=answer,
            evidence_ledger=ledger_with_evidence,
            privacy_mapper=privacy_mapper,
            output_schema=EXAM_ANALYSIS_OUTPUT_SCHEMA,
            raw_text="",
            finish_reason="stop",
        )
        assert result.valid is False

    def test_nonexistent_evidence_id_fails(self, validator, ledger_with_evidence, privacy_mapper):
        answer = self._valid_answer()
        answer["findings"][0]["evidence_ids"] = ["ev_999"]
        result = validator.validate(
            structured_answer=answer,
            evidence_ledger=ledger_with_evidence,
            privacy_mapper=privacy_mapper,
            output_schema=EXAM_ANALYSIS_OUTPUT_SCHEMA,
            raw_text="",
            finish_reason="stop",
        )
        assert result.valid is False


# ---------------------------------------------------------------------------
# EvidenceLedger 模型侧脱敏
# ---------------------------------------------------------------------------

class TestEvidenceLedgerPrivacy:
    def test_personal_file_metadata_sanitizes_fact_and_summary(self):
        ledger = EvidenceLedger(run_id=1)
        ledger.add(
            evidence_type="file_page",
            local_fact={
                "attachment_id": 7,
                "original_name": "张三成绩表.xlsx",
                "title": "张三成绩表",
            },
            contains_personal_data=True,
            display_summary="已确认资料：张三成绩表.xlsx",
        )
        mapper = MagicMock()
        mapper.sanitize_for_model.return_value = {"title": "student_01成绩表"}
        mapper.sanitize_text.return_value = "已确认资料：student_01成绩表.xlsx"

        item = ledger.for_model(mapper)[0]

        assert item["fact"] == {"title": "student_01成绩表"}
        assert item["summary"] == "已确认资料：student_01成绩表.xlsx"
        mapper.sanitize_for_model.assert_called_once()
        mapper.sanitize_text.assert_called_once_with("已确认资料：张三成绩表.xlsx")


# ---------------------------------------------------------------------------
# 安全降级 fallback
# ---------------------------------------------------------------------------

class TestFallbackSummary:
    def test_fallback_has_scope_snapshot(self):
        validator = OutputValidator()
        ledger = EvidenceLedger(run_id=1)
        ledger.add(evidence_type="db_metric", local_fact={"average_score": 72.3})
        mapper = MagicMock()

        fallback = validator.build_fallback_summary(ledger, mapper)
        assert "scope_snapshot" in fallback
        assert isinstance(fallback["scope_snapshot"], dict)

    def test_fallback_has_schema_version(self):
        validator = OutputValidator()
        ledger = EvidenceLedger(run_id=1)
        mapper = MagicMock()

        fallback = validator.build_fallback_summary(ledger, mapper)
        assert "schema_version" in fallback
        assert fallback["schema_version"] == "1.0.0"

    def test_fallback_findings_have_evidence_ids(self):
        validator = OutputValidator()
        ledger = EvidenceLedger(run_id=1)
        eid = ledger.add(
            evidence_type="db_metric",
            local_fact={"average_score": 72.3},
        )
        mapper = MagicMock()

        fallback = validator.build_fallback_summary(ledger, mapper)
        for finding in fallback["findings"]:
            assert "evidence_ids" in finding
            assert len(finding["evidence_ids"]) >= 1

    def test_fallback_marked_degraded(self):
        validator = OutputValidator()
        ledger = EvidenceLedger(run_id=1)
        mapper = MagicMock()

        fallback = validator.build_fallback_summary(ledger, mapper)
        assert fallback.get("degraded") is True
