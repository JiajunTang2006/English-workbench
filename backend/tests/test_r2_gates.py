"""R2 门禁测试：证据与输出验证

验证 R2 改造的输出校验和安全降级：
1. 虚假证据被阻断
2. 缺失字段被阻断
3. 截断输出 (finish_reason=length) 被阻断
4. 姓名泄露被阻断
5. HTML/脚本注入被阻断
6. 安全降级：验证失败后返回本地确定性摘要
7. Pydantic StructuredAnswer 强制结构
"""

from __future__ import annotations

import pytest

from backend.app.agent.evidence import EvidenceLedger
from backend.app.agent.models import StructuredAnswer, Finding, Recommendation
from backend.app.agent.output_validator import OutputValidator, ValidationResult
from backend.app.agent.privacy import PrivacyMapper


# ---------------------------------------------------------------------------
# 1. 虚假证据被阻断
# ---------------------------------------------------------------------------


def test_r2_fake_evidence_rejected():
    """引用不存在的证据 ID 时验证失败。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    # ledger 为空，ev_1 不存在
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "x", "evidence_ids": ["ev_999"]}],
            "recommendations": [{"action": "y", "supports": ["ev_999"]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is False
    assert any("ev_999" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 2. 缺失字段被阻断
# ---------------------------------------------------------------------------


def test_r2_missing_answer_type_rejected():
    """缺少 answer_type 字段时验证失败。"""
    validator = OutputValidator()
    result = validator.validate(
        structured_answer={
            "summary": "测试",
            "findings": [],
            "recommendations": [],
            "limitations": [],
        },
        evidence_ledger=EvidenceLedger(),
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is False
    assert any("answer_type" in e for e in result.errors)


def test_r2_missing_summary_rejected():
    """缺少 summary 字段时验证失败。"""
    validator = OutputValidator()
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "findings": [],
            "recommendations": [],
            "limitations": [],
        },
        evidence_ledger=EvidenceLedger(),
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is False
    assert any("summary" in e for e in result.errors)


def test_r2_finding_without_evidence_rejected():
    """Finding 缺少 evidence_ids 时验证失败。"""
    validator = OutputValidator()
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "无证据发现"}],
            "recommendations": [],
            "limitations": [],
        },
        evidence_ledger=EvidenceLedger(),
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is False


def test_r2_recommendation_without_supports_rejected():
    """Recommendation 缺少 supports 时验证失败。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "ok", "evidence_ids": [eid]}],
            "recommendations": [{"action": "无证据建议"}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
    )
    assert result.valid is False
    assert any("supports" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 3. 截断输出被阻断
# ---------------------------------------------------------------------------


def test_r2_truncated_output_rejected():
    """finish_reason=length 时验证失败。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "ok", "evidence_ids": [eid]}],
            "recommendations": [{"action": "ok", "supports": [eid]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
        finish_reason="length",
    )
    assert result.valid is False
    assert any("length" in e for e in result.errors)


def test_r2_error_finish_reason_rejected():
    """finish_reason=error 时验证失败。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "ok", "evidence_ids": [eid]}],
            "recommendations": [{"action": "ok", "supports": [eid]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
        finish_reason="error",
    )
    assert result.valid is False


# ---------------------------------------------------------------------------
# 4. 姓名泄露被阻断
# ---------------------------------------------------------------------------


def test_r2_student_no_leak_rejected():
    """输出中包含词典内真实学号时验证失败（裸数字不再误判）。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    mapper = PrivacyMapper()
    mapper.register_protected_terms(["20261101"])  # 真实学号进入运行词典
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "ok", "evidence_ids": [eid]}],
            "recommendations": [{"action": "ok", "supports": [eid]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=mapper,
        raw_text="学生学号 20261101 成绩较低",
    )
    assert result.valid is False
    assert any("学号" in e or "身份" in e for e in result.errors)


def test_r2_phone_leak_rejected():
    """输出中包含电话号码时验证失败。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "ok", "evidence_ids": [eid]}],
            "recommendations": [{"action": "ok", "supports": [eid]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
        raw_text="联系电话 13800138000",
    )
    assert result.valid is False
    assert any("电话" in e for e in result.errors)


def test_r2_privacy_check_recursive_in_structured_answer():
    """structured_answer 嵌套字段中带出词典内真实学号也应被检测。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    mapper = PrivacyMapper()
    mapper.register_protected_terms(["20261101"])
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "正常摘要",
            "findings": [
                {
                    "title": "学号 20261101 成绩偏低",
                    "evidence_ids": [eid],
                    "detail": "该生 20261101 需要关注",
                },
            ],
            "recommendations": [{"action": "ok", "supports": [eid]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=mapper,
        raw_text="",  # raw_text 为空，只检查 structured_answer
    )
    assert result.valid is False
    assert any("学号" in e or "身份" in e for e in result.errors)


def test_r2_privacy_check_recursive_phone_in_nested_dict():
    """structured_answer 嵌套 dict 中的电话号码也应被检测。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "正常",
            "findings": [{"title": "ok", "evidence_ids": [eid]}],
            "recommendations": [
                {
                    "action": "联系家长",
                    "supports": [eid],
                    "contact": {"phone": "家长电话 13912345678"},
                },
            ],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
        raw_text="",
    )
    assert result.valid is False
    assert any("电话" in e for e in result.errors)


def test_r2_privacy_check_clean_structured_answer_passes():
    """无隐私泄露的 structured_answer 应通过。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "班级平均分有所提升",
            "findings": [{"title": "平均分 78.5", "evidence_ids": [eid]}],
            "recommendations": [{"action": "继续保持", "supports": [eid]}],
            "limitations": ["样本量有限"],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
        raw_text="班级整体表现良好",
    )
    assert result.valid is True


# ---------------------------------------------------------------------------
# 5. HTML/脚本注入被阻断
# ---------------------------------------------------------------------------


def test_r2_html_injection_rejected():
    """输出中包含 HTML 标签时验证失败。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    eid = ledger.add(evidence_type="db_metric", local_fact={"x": 1})
    result = validator.validate(
        structured_answer={
            "answer_type": "exam_analysis",
            "summary": "测试",
            "findings": [{"title": "ok", "evidence_ids": [eid]}],
            "recommendations": [{"action": "ok", "supports": [eid]}],
            "limitations": [],
        },
        evidence_ledger=ledger,
        privacy_mapper=PrivacyMapper(),
        raw_text="<script>alert('xss')</script>",
    )
    assert result.valid is False
    assert any("HTML" in e or "注入" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 6. 安全降级
# ---------------------------------------------------------------------------


def test_r2_fallback_summary_built_from_evidence():
    """安全降级时从证据账本构建确定性摘要。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    ledger.add(
        evidence_type="db_metric",
        local_fact={"average_score": 75.5, "pass_rate": 0.8, "participant_count": 30},
    )
    mapper = PrivacyMapper()

    fallback = validator.build_fallback_summary(ledger, mapper)

    assert fallback["answer_type"] == "fallback_summary"
    assert fallback["degraded"] is True
    assert "报告生成失败" in fallback["summary"]
    assert len(fallback["findings"]) > 0
    # findings 应包含从证据提取的数值
    titles = [f["title"] for f in fallback["findings"]]
    assert any("average_score" in t for t in titles)
    assert any("pass_rate" in t for t in titles)


def test_r2_fallback_summary_empty_evidence():
    """证据账本为空时降级摘要仍可构建。"""
    validator = OutputValidator()
    ledger = EvidenceLedger()
    mapper = PrivacyMapper()

    fallback = validator.build_fallback_summary(ledger, mapper)

    assert fallback["answer_type"] == "fallback_summary"
    assert fallback["degraded"] is True
    assert len(fallback["findings"]) == 0
    assert len(fallback["limitations"]) > 0


# ---------------------------------------------------------------------------
# 7. Pydantic StructuredAnswer 强制结构
# ---------------------------------------------------------------------------


def test_r2_structured_answer_requires_answer_type():
    """StructuredAnswer 必须有 answer_type。"""
    with pytest.raises(Exception):
        StructuredAnswer(summary="test")


def test_r2_structured_answer_requires_summary():
    """StructuredAnswer 必须有非空 summary。"""
    with pytest.raises(Exception):
        StructuredAnswer(answer_type="exam_analysis", summary="")


def test_r2_finding_requires_evidence_ids():
    """Finding 必须有非空 evidence_ids。"""
    with pytest.raises(Exception):
        Finding(title="test", evidence_ids=[])


def test_r2_recommendation_requires_supports():
    """Recommendation 必须有非空 supports。"""
    with pytest.raises(Exception):
        Recommendation(action="test", supports=[])


def test_r2_valid_structured_answer_passes():
    """结构合法的 StructuredAnswer 可以正常构造。"""
    answer = StructuredAnswer(
        answer_type="exam_analysis",
        summary="考试分析摘要",
        findings=[Finding(title="平均分偏低", evidence_ids=["ev_1"])],
        recommendations=[Recommendation(action="加强基础", supports=["ev_1"])],
        limitations=["数据有限"],
    )
    assert answer.answer_type == "exam_analysis"
    assert len(answer.findings) == 1
    assert len(answer.recommendations) == 1
