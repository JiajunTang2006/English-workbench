"""Regression for real-model usage omissions and unknown pricing."""
from sqlalchemy import select
import asyncio
from types import SimpleNamespace

from backend.app.agent.cost import CostEstimator
from backend.app.agent.run_executor import _finalize_harness_usage, _repair_turn
from backend.app.models import AnalysisRun
from backend.app.models.agent_entities import LlmUsageRecord
from backend.tests.test_teacher_realistic_scenarios import classroom
from backend.tests.test_practice_workspace import env, draft, fake, H


def test_unknown_cost_is_unknown_even_with_budget_control_disabled():
    for mode in (True, False):
        assert CostEstimator(budget_control_enabled=mode).safe_actual_cost("unlisted-model", 100, 200) is None


def test_sdk_usage_replaces_partial_projection_without_double_counting(classroom):
    w = classroom
    run = AnalysisRun(session_id=w.chat.id,term_id=w.term.id,capability='general_chat',status='completed',
        input_summary_json={'model_name':'unlisted-model','provider':'zhipu'})
    w.db.add(run); w.db.flush()
    w.db.add(LlmUsageRecord(run_id=run.id,provider='zhipu',model_name='unlisted-model',stage='text_analysis',input_tokens=100,output_tokens=10,cost_yuan=0))
    w.db.commit()
    result={'usage_records':[{'input_tokens':100,'cache_read_tokens':20,'output_tokens':10},{'input_tokens':150,'output_tokens':40}]}
    for _ in range(2):
        summary=_finalize_harness_usage(w.db,run,result); w.db.commit()
        assert summary=={'usage_known':True,'usage_record_count':2}
        assert run.actual_tokens==320 and run.actual_cost_yuan is None
        assert len(list(w.db.scalars(select(LlmUsageRecord).where(LlmUsageRecord.run_id==run.id))))==2


def test_missing_provider_usage_is_not_zero_tokens(classroom):
    w=classroom
    run=AnalysisRun(session_id=w.chat.id,term_id=w.term.id,capability='general_chat',status='completed',input_summary_json={})
    w.db.add(run); w.db.flush()
    assert _finalize_harness_usage(w.db,run,{})=={'usage_known':False,'usage_record_count':0}
    assert run.actual_tokens is None and run.actual_cost_yuan is None


def test_incomplete_repair_usage_does_not_claim_a_complete_bill(classroom):
    w = classroom
    run = AnalysisRun(session_id=w.chat.id,term_id=w.term.id,capability='exam_analysis',status='degraded',
        input_summary_json={'model_name':'deepseek-chat'})
    w.db.add(run); w.db.flush()
    result = {'usage_records':[{'input_tokens':100,'output_tokens':10}], 'usage_complete':False}
    assert _finalize_harness_usage(w.db,run,result) == {'usage_known':False,'usage_complete':False}
    assert run.actual_tokens is None and run.actual_cost_yuan is None


def test_successful_report_repair_returns_its_additional_usage(monkeypatch):
    from backend.app.agent.output_validator import OutputValidator
    monkeypatch.setattr(OutputValidator, 'validate', lambda *args, **kwargs: SimpleNamespace(valid=True))
    records = [{'input_tokens':90, 'output_tokens':20}]
    class Manager:
        async def call(self, *args, **kwargs):
            return {'finalResponse':'{}', 'usage_records':records}
    validated = asyncio.run(_repair_turn(Manager(), 'synthetic-session', None, 1, 1, '{}', None, []))
    assert validated.usage_records == records


def test_practice_unknown_price_keeps_tokens_without_showing_free(env, monkeypatch):
    c, base, ids, _, sid, cfg = env
    object.__setattr__(cfg, 'text_model_name', 'unlisted-model')
    body = draft(ids)
    fake(monkeypatch, {k:v for k,v in body.items() if k in {'title','objective','questions'}})
    response = c.post(base+'/practice/generate', headers=H, json={
        'objective':'等价分数', 'student_ids':ids[:1], 'level':'basic', 'question_count':1, 'session_id':sid})
    assert response.status_code == 201, response.text
    usage = response.json()['generation_usage']
    assert usage['actual_tokens'] == 300 and usage['usage_known'] is True
    assert usage['actual_cost_yuan'] is None and usage['estimated_cost_yuan'] is None
