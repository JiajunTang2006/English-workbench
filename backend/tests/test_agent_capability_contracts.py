"""R0 基线：四项 capability 契约快照

冻结当前 4 项能力的配置，确保改造过程中能力定义不漂移。
每个能力验证：required_tools、scope_requirements、budget、iterations。
"""

from __future__ import annotations

import pytest

from backend.app.agent.registry.capabilities import create_default_capability_registry
from backend.app.agent.registry.tools import create_default_registry


# ---------------------------------------------------------------------------
# 能力注册表整体快照
# ---------------------------------------------------------------------------

EXPECTED_CAPABILITIES = {
    "exam_analysis",
    "student_diagnosis",
    "review_plan",
    "exam_ingestion",
}

EXPECTED_TOOLS = {
    # exam (5)
    "get_exam_statistics",
    "get_question_list",
    "get_question_difficulty",
    "get_score_distribution",
    "get_knowledge_coverage",
    # student (3)
    "get_student_list",
    "get_student_scores",
    "get_class_ranking",
    # student profile (2)
    "get_student_profile",
    "propose_student_profile_update",
    # risk (2)
    "get_at_risk_students",
    "get_risk_factors",
    # error (2)
    "get_error_causes",
    "get_common_mistakes",
    # attachment (2)
    "get_exam_paper_image",
    "get_student_answer_image",
    # report (1)
    "submit_teaching_report",
}


def test_capability_registry_has_four_capabilities():
    """四项分析能力全部注册；普通对话为聊天入口的内部能力。"""
    reg = create_default_capability_registry()
    names = {cap.name for cap in reg.list_all()}
    assert names == EXPECTED_CAPABILITIES


def test_tool_registry_has_seventeen_tools():
    """17 个工具全部注册。"""
    reg = create_default_registry()
    names = {t.name for t in reg.list_all()}
    assert names == EXPECTED_TOOLS


def test_all_capability_tools_exist_in_registry():
    """每个能力声明的 required_tools 都在工具注册表中存在。"""
    cap_reg = create_default_capability_registry()
    tool_reg = create_default_registry()
    errors = cap_reg.validate_tools(tool_reg)
    assert errors == [], f"能力声明的工具缺失: {errors}"


# ---------------------------------------------------------------------------
# exam_analysis 契约
# ---------------------------------------------------------------------------

def test_exam_analysis_contract():
    reg = create_default_capability_registry()
    cap = reg.get("exam_analysis")
    assert cap is not None
    assert cap.display_name == "考试整体分析"
    assert cap.requires_vision is False
    assert cap.scope_requirements == []
    assert cap.optional_scope == ["exam_id"]
    assert cap.max_iterations == 8
    assert cap.max_parallel_tools == 4
    assert cap.budget_limit_yuan == 0.5
    assert set(cap.required_tools) == {
        "get_exam_statistics", "get_question_list",
        "get_question_difficulty",
        "get_score_distribution", "get_knowledge_coverage",
        "get_error_causes", "get_common_mistakes",
        "submit_teaching_report",
    }


# ---------------------------------------------------------------------------
# student_diagnosis 契约
# ---------------------------------------------------------------------------

def test_student_diagnosis_contract():
    reg = create_default_capability_registry()
    cap = reg.get("student_diagnosis")
    assert cap is not None
    assert cap.display_name == "学生诊断"
    assert cap.requires_vision is False
    assert cap.scope_requirements == ["exam_id", "student_id"]
    assert cap.max_iterations == 6
    assert cap.max_parallel_tools == 3
    assert cap.budget_limit_yuan == 0.3
    assert set(cap.required_tools) == {
        "get_student_scores", "get_student_list",
        "get_error_causes", "get_knowledge_coverage",
        "get_student_profile", "propose_student_profile_update",
        "submit_teaching_report",
    }


# ---------------------------------------------------------------------------
# review_plan 契约
# ---------------------------------------------------------------------------

def test_review_plan_contract():
    reg = create_default_capability_registry()
    cap = reg.get("review_plan")
    assert cap is not None
    assert cap.display_name == "复习计划生成"
    assert cap.requires_vision is False
    assert cap.scope_requirements == ["exam_id"]
    assert cap.max_iterations == 6
    assert cap.max_parallel_tools == 4
    assert cap.budget_limit_yuan == 0.4
    assert set(cap.required_tools) == {
        "get_exam_statistics", "get_knowledge_coverage",
        "get_error_causes", "get_at_risk_students",
        "submit_teaching_report",
    }


# ---------------------------------------------------------------------------
# exam_ingestion 契约
# ---------------------------------------------------------------------------

def test_exam_ingestion_contract():
    reg = create_default_capability_registry()
    cap = reg.get("exam_ingestion")
    assert cap is not None
    assert cap.display_name == "试卷录入"
    assert cap.requires_vision is True
    assert cap.scope_requirements == ["exam_id"]
    assert cap.max_iterations == 2
    assert cap.max_parallel_tools == 2
    assert cap.budget_limit_yuan == 1.0
    assert set(cap.required_tools) == {"get_exam_paper_image"}
