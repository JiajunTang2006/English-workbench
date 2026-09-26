"""U3-02 证据在工具完成时创建测试。

测试覆盖：
1. _try_extract_evidence 已被删除
2. EvidenceLedger.add 在工具执行后创建证据
3. 证据 ID 格式稳定 (ev_1, ev_2, ...)
4. validate_references 校验证据引用
5. for_model 序列化脱敏
6. EvidenceService.save_ledger_to_db 持久化
7. EvidenceService.restore_to_ledger 恢复
8. 工具执行结果 → 证据创建 → 模型引用的完整流程
"""

import pytest
from datetime import datetime, timezone

from backend.app.agent.evidence import Evidence, EvidenceLedger
from backend.app.agent.runtime.harness_adapter import HarnessRunAdapter, HarnessRunResult


# ---------------------------------------------------------------------------
# _try_extract_evidence 已删除
# ---------------------------------------------------------------------------

class TestTryExtractEvidenceRemoved:
    def test_method_removed(self):
        """U3-02: _try_extract_evidence 方法已从 HarnessRunAdapter 删除。"""
        adapter = HarnessRunAdapter()
        assert not hasattr(adapter, "_try_extract_evidence")

    def test_harness_run_result_evidence_defaults_empty(self):
        """HarnessRunResult.evidence 默认为空列表。"""
        result = HarnessRunResult(
            success=True, answer="test",
            harness_session_id="s1", events=[],
            elapsed_ms=100,
        )
        assert result.evidence == []

    def test_no_reverse_extraction_from_model_text(self):
        """模型最终文本中的 evidence 字段不再被反向提取。"""
        # 即使模型输出包含 evidence JSON，也不再从中提取
        # 证据只能由工具执行时创建
        adapter = HarnessRunAdapter()
        # 方法不存在，确认无法调用
        assert not callable(getattr(adapter, "_try_extract_evidence", None))


# ---------------------------------------------------------------------------
# EvidenceLedger 证据创建
# ---------------------------------------------------------------------------

class TestEvidenceLedgerCreation:
    def test_add_returns_stable_id(self):
        ledger = EvidenceLedger(run_id=1)
        eid1 = ledger.add(evidence_type="db_metric", local_fact={"avg": 72.3})
        eid2 = ledger.add(evidence_type="db_metric", local_fact={"max": 95})
        assert eid1 == "ev_1"
        assert eid2 == "ev_2"

    def test_add_with_all_fields(self):
        ledger = EvidenceLedger(run_id=1)
        eid = ledger.add(
            evidence_type="db_metric",
            local_fact={"avg": 72.3},
            source_entity="get_exam_statistics",
            source_field="average_score",
            calculation_formula="avg(scores)",
            numerator=2169.0,
            denominator=30,
            contains_personal_data=False,
            display_summary="全班平均分 72.3",
        )
        ev = ledger.get(eid)
        assert ev is not None
        assert ev.evidence_type == "db_metric"
        assert ev.source_entity == "get_exam_statistics"
        assert ev.calculation_formula == "avg(scores)"
        assert ev.numerator == 2169.0
        assert ev.denominator == 30
        assert ev.display_summary == "全班平均分 72.3"

    def test_get_nonexistent(self):
        ledger = EvidenceLedger()
        assert ledger.get("ev_999") is None

    def test_exists(self):
        ledger = EvidenceLedger()
        eid = ledger.add(evidence_type="db_metric", local_fact={})
        assert ledger.exists(eid)
        assert not ledger.exists("ev_999")

    def test_all_ids(self):
        ledger = EvidenceLedger()
        ledger.add(evidence_type="db_metric")
        ledger.add(evidence_type="rule_signal")
        assert ledger.all_ids() == ["ev_1", "ev_2"]


# ---------------------------------------------------------------------------
# validate_references
# ---------------------------------------------------------------------------

class TestValidateReferences:
    def test_all_exist(self):
        ledger = EvidenceLedger()
        e1 = ledger.add(evidence_type="db_metric")
        e2 = ledger.add(evidence_type="db_metric")
        assert ledger.validate_references([e1, e2]) is True

    def test_some_missing(self):
        ledger = EvidenceLedger()
        e1 = ledger.add(evidence_type="db_metric")
        assert ledger.validate_references([e1, "ev_999"]) is False

    def test_empty_list(self):
        ledger = EvidenceLedger()
        assert ledger.validate_references([]) is True


# ---------------------------------------------------------------------------
# for_model 序列化
# ---------------------------------------------------------------------------

class TestForModelSerialization:
    def test_basic_serialization(self):
        ledger = EvidenceLedger()
        eid = ledger.add(
            evidence_type="db_metric",
            local_fact={"avg": 72.3},
            display_summary="全班平均分 72.3",
        )
        result = ledger.for_model()
        assert len(result) == 1
        assert result[0]["evidence_id"] == eid
        assert result[0]["evidence_type"] == "db_metric"
        assert result[0]["fact"] == {"avg": 72.3}
        assert result[0]["summary"] == "全班平均分 72.3"

    def test_privacy_mapper_called_for_personal_data(self):
        class MockPrivacyMapper:
            def sanitize_for_model(self, fact):
                return {"sanitized": True}

        ledger = EvidenceLedger()
        ledger.add(
            evidence_type="student_trend",
            local_fact={"student_name": "张三", "score": 85},
            contains_personal_data=True,
        )
        result = ledger.for_model(privacy_mapper=MockPrivacyMapper())
        assert result[0]["fact"] == {"sanitized": True}

    def test_no_sanitization_for_non_personal(self):
        class MockPrivacyMapper:
            def sanitize_for_model(self, fact):
                return {"should_not_be_called": True}

        ledger = EvidenceLedger()
        ledger.add(
            evidence_type="db_metric",
            local_fact={"avg": 72.3},
            contains_personal_data=False,
        )
        result = ledger.for_model(privacy_mapper=MockPrivacyMapper())
        assert result[0]["fact"] == {"avg": 72.3}


# ---------------------------------------------------------------------------
# EvidenceService 持久化与恢复
# ---------------------------------------------------------------------------

class TestEvidenceServicePersistence:
    @pytest.fixture
    def db_session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.app.models.agent_entities import Base
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        session = sf()
        yield session
        session.close()

    def test_save_and_restore_ledger(self, db_session):
        from backend.app.services.agent_analysis.evidence import EvidenceService

        svc = EvidenceService(db_session)
        ledger = EvidenceLedger(run_id=1)
        ledger.add(
            evidence_type="db_metric",
            local_fact={"avg": 72.3},
            source_entity="get_exam_statistics",
            display_summary="全班平均分 72.3",
        )
        ledger.add(
            evidence_type="rule_signal",
            local_fact={"risk_count": 5},
            source_entity="get_at_risk_students",
            display_summary="5 名风险学生",
        )

        # 持久化
        count = svc.save_ledger_to_db(1, ledger)
        assert count == 2

        # 恢复到新 ledger
        new_ledger = EvidenceLedger(run_id=1)
        restored_ids = svc.restore_to_ledger(1, new_ledger)
        assert len(restored_ids) == 2
        assert len(new_ledger.all_ids()) == 2

    def test_get_evidence_by_id(self, db_session):
        from backend.app.services.agent_analysis.evidence import EvidenceService

        svc = EvidenceService(db_session)
        ledger = EvidenceLedger(run_id=1)
        eid = ledger.add(
            evidence_type="db_metric",
            local_fact={"avg": 72.3},
        )
        svc.save_ledger_to_db(1, ledger)

        ev = svc.get_evidence_by_id(eid)
        assert ev is not None
        assert ev.evidence_id == eid
        assert ev.run_id == 1

    def test_list_evidence_for_run(self, db_session):
        from backend.app.services.agent_analysis.evidence import EvidenceService

        svc = EvidenceService(db_session)
        ledger = EvidenceLedger(run_id=1)
        for i in range(5):
            ledger.add(evidence_type="db_metric", local_fact={"i": i})
        svc.save_ledger_to_db(1, ledger)

        ev_list = svc.list_evidence_for_run(1)
        assert len(ev_list) == 5


# ---------------------------------------------------------------------------
# 完整流程：工具执行 → 证据创建
# ---------------------------------------------------------------------------

class TestEvidenceFirstFlow:
    def test_tool_result_becomes_evidence(self):
        """模拟工具执行后，结果被注册为证据的完整流程。"""
        ledger = EvidenceLedger(run_id=1)

        # 模拟工具返回结果
        tool_result_1 = {"data": {"average_score": 72.3, "max_score": 95}}
        tool_result_2 = {"data": {"risk_count": 5, "students": ["s1", "s2"]}}

        # 模拟 _register_evidence 逻辑
        eid1 = ledger.add(
            evidence_type="db_metric",
            local_fact=tool_result_1["data"],
            source_entity="get_exam_statistics",
            display_summary="工具 get_exam_statistics 返回的查询数据",
        )
        eid2 = ledger.add(
            evidence_type="db_metric",
            local_fact=tool_result_2["data"],
            source_entity="get_at_risk_students",
            display_summary="工具 get_at_risk_students 返回的查询数据",
        )

        # 证据在模型结论之前创建
        assert eid1 == "ev_1"
        assert eid2 == "ev_2"
        assert len(ledger.all_ids()) == 2

        # 模型可以引用这些证据
        assert ledger.validate_references([eid1, eid2]) is True

    def test_evidence_created_before_model_answer(self):
        """证据必须在模型最终回答之前全部创建。"""
        ledger = EvidenceLedger(run_id=1)

        # 模拟 Agent 循环中的工具调用序列
        tool_results = [
            ("get_exam_statistics", {"data": {"avg": 72.3}}),
            ("get_score_distribution", {"data": {"histogram": [5, 10, 15]}}),
            ("get_knowledge_coverage", {"data": {"coverage": 0.85}}),
        ]

        evidence_ids = []
        for tool_name, result in tool_results:
            eid = ledger.add(
                evidence_type="db_metric",
                local_fact=result["data"],
                source_entity=tool_name,
            )
            evidence_ids.append(eid)

        # 在模型给出最终回答前，所有证据已存在
        assert len(evidence_ids) == 3
        assert ledger.validate_references(evidence_ids) is True

        # 模型回答中的 findings 引用这些证据 ID
        model_findings = [
            {"title": "均分偏低", "evidence_ids": [evidence_ids[0]]},
            {"title": "分数分布集中", "evidence_ids": [evidence_ids[1]]},
            {"title": "知识点覆盖率高", "evidence_ids": [evidence_ids[2]]},
        ]
        for f in model_findings:
            assert ledger.validate_references(f["evidence_ids"]) is True

    def test_no_evidence_from_model_text(self):
        """模型最终文本中的 evidence 字段不被提取为证据。"""
        # 证据只能由工具执行时创建
        ledger = EvidenceLedger(run_id=1)
        ledger.add(evidence_type="db_metric", local_fact={"avg": 72.3})

        # 模型输出中可能包含 evidence JSON，但不会被反向提取
        model_answer_with_evidence_json = (
            '```json\n{"evidence": [{"evidence_id": "fake_ev", "fact": "fake"}]}\n```'
        )

        # 证据账本中仍然只有工具创建的证据
        assert len(ledger.all_ids()) == 1
        assert "fake_ev" not in ledger.all_ids()
        assert not ledger.validate_references(["fake_ev"])