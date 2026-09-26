"""P0-3: 统一结构化输出契约测试

验证：
1. 所有 Capability 的 output_schema 字段名与 Pydantic StructuredAnswer 一致
2. Finding 使用 title/description/evidence_ids/severity（非 category/evidence_refs）
3. Recommendation 使用 action/rationale/supports/priority（非其他命名）
4. required 和 additionalProperties 正确设置
5. 合法/非法样例验证
6. 前端期望的字段存在
"""

import pytest
from pydantic import ValidationError

from backend.app.agent.models import StructuredAnswer, Finding, Recommendation
from backend.app.agent.schema_contract import (
    make_structured_output_schema,
    FINDING_SCHEMA,
    RECOMMENDATION_SCHEMA,
    EXAM_ANALYSIS_OUTPUT_SCHEMA,
    STUDENT_DIAGNOSIS_OUTPUT_SCHEMA,
    REVIEW_PLAN_OUTPUT_SCHEMA,
    EXAM_INGESTION_OUTPUT_SCHEMA,
)
from backend.app.agent.registry.capabilities import create_default_capability_registry


class TestSchemaContractConsistency:
    """Schema 契约与 Pydantic 模型一致性测试。"""

    def test_finding_schema_matches_pydantic(self):
        """JSON Schema 的 Finding 字段与 Pydantic Finding 一致。"""
        props = FINDING_SCHEMA["properties"]
        assert "title" in props
        assert "description" in props
        assert "evidence_ids" in props
        assert "severity" in props
        assert "category" not in props
        assert "evidence_refs" not in props
        assert "claim" not in props

    def test_recommendation_schema_matches_pydantic(self):
        """JSON Schema 的 Recommendation 字段与 Pydantic Recommendation 一致。"""
        props = RECOMMENDATION_SCHEMA["properties"]
        assert "action" in props
        assert "rationale" in props
        assert "supports" in props
        assert "priority" in props
        assert "evidence_refs" not in props

    def test_finding_schema_has_additional_properties_false(self):
        assert FINDING_SCHEMA.get("additionalProperties") is False

    def test_recommendation_schema_has_additional_properties_false(self):
        assert RECOMMENDATION_SCHEMA.get("additionalProperties") is False

    def test_finding_requires_title_and_evidence_ids(self):
        required = FINDING_SCHEMA.get("required", [])
        assert "title" in required
        assert "evidence_ids" in required

    def test_recommendation_requires_action_and_supports(self):
        required = RECOMMENDATION_SCHEMA.get("required", [])
        assert "action" in required
        assert "supports" in required


class TestCapabilitySchemaAlignment:
    """各能力 output_schema 与统一契约对齐测试。"""

    @pytest.fixture
    def registry(self):
        return create_default_capability_registry()

    def test_exam_analysis_uses_unified_schema(self, registry):
        cap = registry.get("exam_analysis")
        schema = cap.output_schema
        props = schema["properties"]
        assert "answer_type" in props
        assert "summary" in props
        assert "findings" in props
        assert "recommendations" in props
        finding_props = props["findings"]["items"]["properties"]
        assert "title" in finding_props
        assert "evidence_ids" in finding_props
        assert "score_analysis" not in props
        assert "knowledge_analysis" not in props

    def test_student_diagnosis_uses_unified_schema(self, registry):
        cap = registry.get("student_diagnosis")
        schema = cap.output_schema
        props = schema["properties"]
        assert "answer_type" in props
        assert "summary" in props
        assert "findings" in props
        assert "recommendations" in props
        assert "strengths" not in props
        assert "weaknesses" not in props
        assert "error_causes" not in props

    def test_review_plan_uses_unified_schema(self, registry):
        cap = registry.get("review_plan")
        schema = cap.output_schema
        props = schema["properties"]
        assert "answer_type" in props
        assert "summary" in props
        assert "findings" in props
        assert "recommendations" in props
        assert "timeline" in props
        assert "priority_topics" not in props
        assert "priority_knowledge_points" not in props

    def test_exam_ingestion_uses_unified_schema(self, registry):
        cap = registry.get("exam_ingestion")
        schema = cap.output_schema
        props = schema["properties"]
        assert "answer_type" in props
        assert "summary" in props
        assert "questions_extracted" in props
        assert "confidence" in props
        assert "issues" in props

    def test_all_schemas_have_additional_properties_false(self, registry):
        for cap in registry.list_all():
            assert cap.output_schema.get("additionalProperties") is False, \
                f"能力 {cap.name} 缺少 additionalProperties: false"

    def test_all_schemas_require_answer_type_and_summary(self, registry):
        for cap in registry.list_all():
            required = cap.output_schema.get("required", [])
            assert "answer_type" in required, f"{cap.name} 缺少 answer_type in required"
            assert "summary" in required, f"{cap.name} 缺少 summary in required"


class TestCapabilityModuleAlignment:
    """capabilities/ 目录模块与统一契约对齐测试。"""

    def test_exam_analysis_module_imports_unified_schema(self):
        from backend.app.agent.capabilities.exam_analysis import EXAM_ANALYSIS_OUTPUT_SCHEMA
        props = EXAM_ANALYSIS_OUTPUT_SCHEMA["properties"]
        assert "answer_type" in props
        assert "findings" in props

    def test_student_diagnosis_module_imports_unified_schema(self):
        from backend.app.agent.capabilities.student_diagnosis import STUDENT_DIAGNOSIS_OUTPUT_SCHEMA
        props = STUDENT_DIAGNOSIS_OUTPUT_SCHEMA["properties"]
        assert "answer_type" in props

    def test_review_plan_module_imports_unified_schema(self):
        from backend.app.agent.capabilities.review_plan import REVIEW_PLAN_OUTPUT_SCHEMA
        props = REVIEW_PLAN_OUTPUT_SCHEMA["properties"]
        assert "answer_type" in props
        assert "timeline" in props

    def test_exam_ingestion_module_imports_unified_schema(self):
        from backend.app.agent.capabilities.exam_ingestion import EXAM_INGESTION_OUTPUT_SCHEMA
        props = EXAM_INGESTION_OUTPUT_SCHEMA["properties"]
        assert "answer_type" in props
        assert "questions_extracted" in props


class TestPydanticValidation:
    """Pydantic 模型验证测试（合法/非法样例）。"""

    def test_valid_structured_answer(self):
        answer = StructuredAnswer(
            answer_type="exam_analysis",
            summary="本次考试整体表现良好",
            findings=[
                Finding(
                    title="词汇薄弱",
                    description="词汇题平均得分率仅 45%",
                    evidence_ids=["ev_001", "ev_002"],
                    severity="warning",
                ),
            ],
            recommendations=[
                Recommendation(
                    action="加强词汇复习",
                    rationale="词汇得分率低",
                    supports=["ev_001"],
                    priority="high",
                ),
            ],
        )
        assert answer.answer_type == "exam_analysis"
        assert len(answer.findings) == 1

    def test_finding_requires_evidence_ids(self):
        with pytest.raises(ValidationError):
            Finding(title="测试", evidence_ids=[])

    def test_finding_requires_title(self):
        with pytest.raises(ValidationError):
            Finding(title="", evidence_ids=["ev_001"])

    def test_recommendation_requires_supports(self):
        with pytest.raises(ValidationError):
            Recommendation(action="测试", supports=[])

    def test_recommendation_requires_action(self):
        with pytest.raises(ValidationError):
            Recommendation(action="", supports=["ev_001"])

    def test_structured_answer_requires_summary(self):
        with pytest.raises(ValidationError):
            StructuredAnswer(answer_type="test", summary="")

    def test_structured_answer_requires_answer_type(self):
        with pytest.raises(ValidationError):
            StructuredAnswer(answer_type="", summary="测试")
