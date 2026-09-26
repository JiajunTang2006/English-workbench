"""P1-6 数字隐私规则统一（B3 审查修复）。

- 词典内已知真实学号/姓名/电话：硬拒（fail-closed）；
- 词典外的裸 6-10 位数字：仅告警，不误杀日期/人数/分数/考试编号；
- 出站观察器与输出验证器语义一致。
"""
from __future__ import annotations

import pytest

from backend.app.agent.outbound_observer import OutboundObserver
from backend.app.agent.privacy import PrivacyMapper, PrivacyViolationError


def _mapper() -> PrivacyMapper:
    m = PrivacyMapper()
    m.register_names(["王小明"])
    m.register_school_names(["第一中学"])
    m.register_protected_terms(["20261101"])  # 真实学号（词典内）
    m.register_protected_terms(["13800000000"])  # 真实电话（词典内）
    return m


class TestObserverNumberSemantics:
    def test_dates_not_rejected(self):
        """日期（20260401）不得被误杀。"""
        entry = OutboundObserver().capture(
            prompt_text="考试日期为20260401，已录入系统",
            privacy_mapper=_mapper(),
        )
        assert "suspect_student_no" not in entry

    def test_counts_and_scores_not_rejected(self):
        """人数（32）与分数（77.5）不得被误杀。"""
        entry = OutboundObserver().capture(
            prompt_text="参考人数32，平均分77.5，最高分100",
            privacy_mapper=_mapper(),
        )
        assert "suspect_student_no" not in entry

    def test_exam_code_not_rejected(self):
        """考试编号（2026003）不得被误杀为学号。"""
        entry = OutboundObserver().capture(
            prompt_text="考试编号2026003已归档",
            privacy_mapper=_mapper(),
        )
        assert "suspect_student_no" not in entry

    def test_dictionary_student_no_hard_rejected(self):
        """词典内真实学号未脱敏时检查器必须硬拒（fail-closed 防线）。"""
        mapper = _mapper()
        hits, _ = OutboundObserver()._check_identity("学生 20261101 需要补考", mapper)
        assert "身份词典命中" in hits

    def test_phone_hard_rejected(self):
        """电话格式未脱敏时检查器硬拒；脱敏后出站安全。"""
        mapper = _mapper()
        hits, _ = OutboundObserver()._check_identity("联系电话 13912345678", mapper)
        assert "电话格式" in hits
        # 正常链路：sanitize 已脱敏 → 出站通过且不残留电话
        entry = OutboundObserver().capture(
            prompt_text="联系电话 13912345678", privacy_mapper=mapper,
        )
        assert "13912345678" not in entry["content"]

    def test_guard_without_mapper_rejects_phone(self):
        """无 mapper 时不脱敏 → 电话直接硬拒（fail-closed）。"""
        with pytest.raises(PrivacyViolationError):
            OutboundObserver().capture(
                prompt_text="联系电话 13912345678", privacy_mapper=None,
            )


class TestValidatorNumberSemantics:
    def test_validator_bare_number_ok(self):
        """输出验证器：裸 6-10 位数字不得导致失败（回归保护）。"""
        from backend.app.agent.output_validator import OutputValidator

        v = OutputValidator().validate(
            structured_answer={
                "answer_type": "exam_analysis",
                "summary": "平均分 77.5，考试日期 20260401，参考人数 32",
                "findings": [],
                "recommendations": [],
                "limitations": [],
                "scope_snapshot": {},
                "schema_version": "1.0.0",
            },
            evidence_ledger=None,
            privacy_mapper=_mapper(),
            raw_text="平均分 77.5，考试日期 20260401，参考人数 32",
            finish_reason="stop",
            db_session=None,
            run_id=1,
        )
        assert v.valid, v.errors

    def test_validator_dictionary_student_no_fails(self):
        """输出验证器：词典内真实学号 → 失败（硬拒）。"""
        from backend.app.agent.output_validator import OutputValidator

        mapper = _mapper()
        v = OutputValidator().validate(
            structured_answer={
                "answer_type": "exam_analysis",
                "summary": "学号 20261101 的学生成绩偏低",
                "findings": [],
                "recommendations": [],
                "limitations": [],
                "scope_snapshot": {},
                "schema_version": "1.0.0",
            },
            evidence_ledger=None,
            privacy_mapper=mapper,
            raw_text="学号 20261101 的学生成绩偏低",
            finish_reason="stop",
            db_session=None,
            run_id=1,
        )
        assert not v.valid
        assert any("学号" in e or "身份" in e for e in v.errors)