"""U3-01 固定教育工具白名单测试。

测试覆盖：
1. submit_teaching_report 工具已注册
2. SCOPE_OWNED_FIELDS 包含 run_id
3. 模型提交 run_id 参数被拒绝
4. submit_teaching_report 要求 run_id 作用域
5. 所有分析能力（exam_analysis/student_diagnosis/review_plan）的白名单包含 submit_teaching_report
6. for_model 只返回白名单内工具
7. 工具执行校验：findings 缺少 evidence_ids 被拒绝
8. 工具执行校验：recommendations 缺少 supports 被拒绝
9. 工具执行校验：完整报告被接受
10. ToolContext.run_id 属性
"""

import pytest

from backend.app.agent.registry.tools import (
    ToolRegistry,
    ToolDefinition,
    ToolError,
    SCOPE_OWNED_FIELDS,
    create_default_registry,
)
from backend.app.agent.registry.capabilities import (
    create_default_capability_registry,
)
from backend.app.agent.tools.tool_context import (
    ToolContext,
    set_tool_context,
    reset_tool_context,
)
from backend.app.agent.tools import register_all_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    r = ToolRegistry()
    register_all_tools(r)
    return r


@pytest.fixture
def capability_registry(registry):
    return create_default_capability_registry()


@pytest.fixture
def tool_ctx():
    # B2-06：报告工具按统一契约校验证据归属，fixture 提供合法证据
    from backend.app.agent.evidence import EvidenceLedger
    ledger = EvidenceLedger(run_id=42)
    ledger.add("db_metric", {"pass_rate": 0.8}, source_entity="exam",
               display_summary="及格率 80%")
    ctx = ToolContext(scope={"run_id": 42, "exam_id": 1}, evidence_ledger=ledger)
    token = set_tool_context(ctx)
    yield ctx
    reset_tool_context(token)


# ---------------------------------------------------------------------------
# 工具注册
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_submit_teaching_report_registered(self, registry):
        tool = registry.get("submit_teaching_report")
        assert tool is not None
        assert tool.category == "report"

    def test_total_tool_count(self, registry):
        """17 个工具应全部注册。"""
        assert len(registry.list_all()) == 17

    def test_submit_teaching_report_requires_run_id(self, registry):
        tool = registry.get("submit_teaching_report")
        assert "run_id" in tool.requires_scope


# ---------------------------------------------------------------------------
# SCOPE_OWNED_FIELDS
# ---------------------------------------------------------------------------

class TestScopeOwnedFields:
    def test_run_id_in_scope_owned(self):
        assert "run_id" in SCOPE_OWNED_FIELDS

    def test_all_scope_fields_owned(self):
        for f in ("exam_id", "class_id", "student_id", "term_id", "run_id"):
            assert f in SCOPE_OWNED_FIELDS

    def test_model_submitting_run_id_rejected(self, registry):
        with pytest.raises(ToolError, match="作用域字段"):
            registry.execute("submit_teaching_report", run_id=99)

    def test_model_submitting_exam_id_rejected(self, registry):
        with pytest.raises(ToolError, match="作用域字段"):
            registry.execute(
                "get_exam_statistics", exam_id=1,
            )


# ---------------------------------------------------------------------------
# 能力白名单
# ---------------------------------------------------------------------------

class TestCapabilityWhitelist:
    def test_exam_analysis_has_submit_report(self, capability_registry):
        cap = capability_registry.get("exam_analysis")
        assert "submit_teaching_report" in cap.required_tools

    def test_student_diagnosis_has_submit_report(self, capability_registry):
        cap = capability_registry.get("student_diagnosis")
        assert "submit_teaching_report" in cap.required_tools

    def test_review_plan_has_submit_report(self, capability_registry):
        cap = capability_registry.get("review_plan")
        assert "submit_teaching_report" in cap.required_tools

    def test_exam_ingestion_no_submit_report(self, capability_registry):
        """exam_ingestion 是录入工具，不需要提交报告。"""
        cap = capability_registry.get("exam_ingestion")
        assert "submit_teaching_report" not in cap.required_tools

    def test_validate_tools_passes(self, capability_registry, registry):
        errors = capability_registry.validate_tools(registry)
        assert errors == []


# ---------------------------------------------------------------------------
# for_model 白名单过滤
# ---------------------------------------------------------------------------

class TestForModelFiltering:
    def test_submit_report_in_whitelist(self, registry):
        tools = registry.for_model(
            scope={"exam_id": 1, "run_id": 42},
            available_tools=[
                "get_exam_statistics",
                "submit_teaching_report",
            ],
        )
        names = [t["function"]["name"] for t in tools]
        assert "submit_teaching_report" in names
        assert "get_exam_statistics" in names

    def test_submit_report_excluded_without_run_id_scope(self, registry):
        """没有 run_id 作用域时，submit_teaching_report 不可用。"""
        tools = registry.for_model(
            scope={"exam_id": 1},
            available_tools=[
                "get_exam_statistics",
                "submit_teaching_report",
            ],
        )
        names = [t["function"]["name"] for t in tools]
        assert "submit_teaching_report" not in names
        assert "get_exam_statistics" in names

    def test_non_whitelisted_tool_excluded(self, registry):
        tools = registry.for_model(
            scope={"exam_id": 1, "run_id": 42},
            available_tools=["get_exam_statistics"],
        )
        names = [t["function"]["name"] for t in tools]
        assert "submit_teaching_report" not in names
        assert "get_student_scores" not in names


# ---------------------------------------------------------------------------
# submit_teaching_report 执行
# ---------------------------------------------------------------------------

class TestSubmitTeachingReport:
    def _valid_report(self):
        return {
            "answer_type": "exam_analysis",
            "summary": "全班均分 72.3，标准差 15.2。",
            "findings": [
                {
                    "title": "分数分布偏低",
                    "evidence_ids": ["ev_1"],
                    "severity": "warning",
                },
            ],
            "recommendations": [
                {
                    "action": "加强基础题训练",
                    "rationale": "基础题得分率偏低",
                    "supports": ["ev_1"],
                    "priority": "high",
                },
            ],
            "limitations": ["样本仅限本班"],
            "scope_snapshot": {"exam_id": 1, "class_id": 5},
            "schema_version": "1.0.0",
        }

    def test_valid_report_accepted(self, registry, tool_ctx):
        report = self._valid_report()
        result = registry.execute("submit_teaching_report", **report)
        assert result["data"]["status"] == "accepted"
        assert result["data"]["run_id"] == 42
        assert result["data"]["findings_count"] == 1

    def test_missing_evidence_ids_rejected(self, registry, tool_ctx):
        report = self._valid_report()
        del report["findings"][0]["evidence_ids"]
        with pytest.raises(ToolError, match="Schema"):
            registry.execute("submit_teaching_report", **report)

    def test_missing_supports_rejected(self, registry, tool_ctx):
        report = self._valid_report()
        del report["recommendations"][0]["supports"]
        with pytest.raises(ToolError, match="Schema"):
            registry.execute("submit_teaching_report", **report)

    def test_no_run_id_in_scope(self, registry):
        ctx = ToolContext(scope={"exam_id": 1})
        token = set_tool_context(ctx)
        try:
            report = self._valid_report()
            result = registry.execute("submit_teaching_report", **report)
            assert "error" in result
            assert "run_id" in result["error"]
        finally:
            reset_tool_context(token)

    def test_no_context(self, registry):
        token = set_tool_context(None)
        try:
            report = self._valid_report()
            result = registry.execute("submit_teaching_report", **report)
            assert "error" in result
        finally:
            reset_tool_context(token)


# ---------------------------------------------------------------------------
# ToolContext 属性
# ---------------------------------------------------------------------------

class TestToolContext:
    def test_run_id_property(self):
        ctx = ToolContext(scope={"run_id": 99})
        assert ctx.run_id == 99

    def test_term_id_property(self):
        ctx = ToolContext(scope={"term_id": 3})
        assert ctx.term_id == 3

    def test_run_id_none(self):
        ctx = ToolContext(scope={})
        assert ctx.run_id is None
