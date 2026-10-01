"""Explicitly opt-in real-model teacher journeys; normal test runs stay offline."""
import json
import os
import re
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from sqlalchemy import select

import pytest

from backend.tests.test_teacher_realistic_scenarios import classroom, HEADERS
from backend.app.agent import config as config_module
from backend.app.models import AnalysisRun
from backend.app.models.agent_entities import LlmUsageRecord
from backend.app.services import practice
from backend.app.services.agent_analysis.conversation import run_mapper

pytestmark = pytest.mark.skipif(os.getenv("TEACHMATE_LIVE_SCENARIOS") != "1",
    reason="Real model evaluation requires an explicit opt-in and configured local provider")


@pytest.fixture
def live_world(tmp_path, monkeypatch):
    source = Path(os.environ["TEACHMATE_PROVIDER_DATA_DIR"]).expanduser().resolve()
    path = source / "workbench.db"
    assert not path.with_name(path.name + "-wal").exists(), "Close the installed app before reading its saved model metadata"
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        row = db.execute("SELECT value_json FROM agent_analysis_settings WHERE key=?", ("agent_runtime_config",)).fetchone()
    assert row, "No saved provider configuration"
    cfg = config_module._deserialize_config(json.loads(row[0]))
    profile = re.sub(r"[^A-Za-z0-9._-]", "_", cfg.text_model_profile_id)[:100]
    key_path = source / "provider/models" / f"{profile}.key"
    if not key_path.is_file():
        key_path = source / "provider/api_key"
    monkeypatch.setenv("TEACHMATE_SCENARIO_KEY", key_path.read_text().strip())
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_RUNTIME", "harness")
    cfg = replace(cfg, text_model_profile_id="", text_api_key_env="TEACHMATE_SCENARIO_KEY",
        feature_flags={**cfg.feature_flags, "agent_enabled": True, "text_agent_enabled": True},
        model_timeout_seconds=90, model_max_retries=0)
    monkeypatch.setattr(config_module, "_runtime_override", cfg)
    generator = classroom.__wrapped__(tmp_path, monkeypatch)
    world = next(generator)
    monkeypatch.setattr(practice, "get_agent_config", lambda: cfg)
    try:
        yield world, cfg
    finally:
        generator.close()


def test_real_model_teacher_chat_practice_and_retest(live_world, monkeypatch):
    w, cfg = live_world; c = w.client
    output = Path(os.environ.get("TEACHMATE_LIVE_OUTPUT", "/private/tmp/teachmate-live-scenarios-20260930.json"))
    results = {"model": cfg.text_model_name, "provider": cfg.text_provider, "runtime": "harness",
        "dataset": {"students": 120, "classes": 2, "exams": 3}, "synthetic_data_only": True,
        "price_known": practice.CostEstimator().get_pricing(cfg.text_model_name) is not None, "stages": [],
        "practice_model_responses": []}
    errors = []

    def save():
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))

    factory = practice._create_text_provider
    def captured_provider(config):
        provider = factory(config)
        complete = provider.complete
        async def capture(*args, **kwargs):
            response = await complete(*args, **kwargs)
            # Only public output for the synthetic classroom, never reasoning or credentials.
            results["practice_model_responses"].append({"content": response.content,
                "finish_reason": response.finish_reason})
            save()
            return response
        provider.complete = capture
        return provider
    monkeypatch.setattr(practice, "_create_text_provider", captured_provider)

    def record(label, started, data, passed):
        results["stages"].append({"stage": label, "elapsed_seconds": round(time.monotonic()-started, 2),
            "passed": passed, **data})
        save()
        print(json.dumps({"stage": label, "passed": passed, "elapsed_seconds": results['stages'][-1]['elapsed_seconds']}, ensure_ascii=False), flush=True)
        if not passed:
            errors.append(label)

    chat = c.post('/api/v1/agent/sessions', headers=HEADERS,
        json={"term_id": w.term.id, "exam_id": w.exams[-1].id, "title": "真实模型·合成课堂测试"})
    assert chat.status_code == 200, chat.text
    sid = chat.json()['id']
    mapper = run_mapper(w.db, {"term_id": w.term.id})
    scenarios = [
        ("同名确认", "看看张晨这次阅读方面的表现，先确认是哪个同学，不要猜。", {}, "ambiguous"),
        ("确认对象及逐题事实", "是八1班的张晨。请说他第5题得了多少分，满分多少；不要做长报告。", {"student_refs": [mapper.to_anonymous(w.students[0].id)]}, "matched"),
        ("换学生追问", "八1班李雨桐呢？同样看第5题。", {}, "matched"),
        ("跨班同名比较", "把八1班张晨和八2班张晨比较一下，只比较第5题实际得分，不据此认定能力。", {}, "matched"),
        ("未知学生推题", "给赵磊出三道阅读题；先确认名单对象，找不到就告诉我。", {}, "not_found"),
        ("课堂约束与自由讨论", "推断还没教，今天只能用10分钟做细节定位，不额外留作业。给点可以当堂做的建议，不用生成正式报告。", {}, "unspecified"),
    ]
    for label, message, extra, expected in scenarios:
        started = time.monotonic()
        response = c.post(f'/api/v1/agent/sessions/{sid}/messages', headers=HEADERS, json={"content": message, **extra})
        if response.status_code != 202:
            record(label, started, {"teacher": message, "status_code": response.status_code, "detail": response.json()}, False)
            continue
        rid = response.json()['run_id']; deadline = time.monotonic() + 110
        while True:
            run = c.get(f'/api/v1/agent/runs/{rid}', headers=HEADERS).json()
            if run.get('status') in {'completed','failed','cancelled','degraded'} or time.monotonic() > deadline:
                break
            time.sleep(0.25)
        if run.get('status') not in {'completed','failed','cancelled','degraded'}:
            c.post(f'/api/v1/agent/runs/{rid}/cancel', headers=HEADERS)
        with w.app.state.session_factory() as db:
            saved = db.get(AnalysisRun, rid)
            summary = saved.input_summary_json or {}
            local = summary.get('conversation_state', {})
        messages = c.get(f'/api/v1/agent/sessions/{sid}/messages', headers=HEADERS).json()
        answer = next((m.get('content_text','') for m in reversed(messages) if m.get('role')=='assistant' and m.get('run_id')==rid), '')
        checks = {"resolved": local.get('resolution_status') == expected,
                  "usage_recorded": saved.actual_tokens is not None and saved.actual_tokens > 0}
        expected_focus = {
            "确认对象及逐题事实": [mapper.to_anonymous(w.students[0].id)],
            "换学生追问": [mapper.to_anonymous(w.students[1].id)],
            "跨班同名比较": [mapper.to_anonymous(w.students[0].id), mapper.to_anonymous(w.students[60].id)],
            "未知学生推题": [],
        }
        if label in expected_focus:
            checks['correct_focus'] = local.get('focus_refs') == expected_focus[label]
        if label in {"确认对象及逐题事实", "换学生追问", "跨班同名比较"}:
            checks['queried_real_facts'] = (summary.get('query_stats') or {}).get('attempts', 0) > 0
            checks['scores_in_answer'] = '0.5' in answer and '2.5' in answer
            if label == '跨班同名比较':
                checks['different_homonym_scores'] = '1.5' in answer
        record(label, started, {"teacher": message, "answer": answer, "status": run.get('status'),
            "resolution_status": local.get('resolution_status'), "focus_refs": local.get('focus_refs'),
            "actual_tokens": saved.actual_tokens, "usage_known": summary.get('usage_known'), "error": run.get('error_message'),
            "query_stats": summary.get('query_stats'), "token_usage": summary.get('token_usage'), "checks": checks},
            run.get('status')=='completed' and bool(answer) and all(checks.values()))

    task = c.post('/api/v1/teaching/tasks', headers=HEADERS, json={"term_id":w.term.id,"class_id":w.classes[0].id,
        "exam_id":w.exams[-1].id,"title":"真实模型·课内定位练习","goal":"用原文定位细节",
        "target_type":"group","student_ids":[w.students[0].id,w.students[1].id],
        "constraints":{"lesson_minutes":10,"no_homework":True,"taught_content":"已教细节定位，推断尚未教"}}).json()
    base=f"/api/v1/teaching/tasks/{task['id']}"
    session=c.post(base+'/sessions',headers=HEADERS,json={}).json()
    c.post(base+'/feedback',headers=HEADERS,json={"kind":"correction","note":"推断尚未教，不认定为能力差","correction_reason":"not_taught"})
    request={"objective":"用原文定位细节","student_ids":task['student_ids'],"question_count":2,"level":"basic",
        "session_id":session['id'],"teacher_note":"只出2道简短细节定位选择题，每题附完整短语篇，学生版无需答案；总计10分钟，不出家庭作业。"}
    started=time.monotonic()
    response=c.post(base+'/practice/generate',headers=HEADERS,json=request)
    record('真实专项练习生成',started,{"status_code":response.status_code,"result":response.json()},response.status_code==201)
    if response.status_code==201:
        item=response.json();ep=base+f"/practices/{item['id']}"
        # Student copy can be inspected without claiming human review of model answers.
        copy=c.get(ep+'?student_copy=true',headers=HEADERS).json()
        assert all(not any(k in q for k in ('answers','explanation','hints')) for q in copy['questions'])
        assert all(q.get('passage') and not re.search('[\u4e00-\u9fff]', q['prompt']+q['passage']+''.join(q['options'].values())) for q in item['questions'])
        # Emulate teacher adoption only inside this disposable test workspace.
        confirmed=c.post(ep+'/confirm',headers=HEADERS,json={'checked_answers':True,'checked_scope':True})
        assert confirmed.status_code == 200
        q=item['questions'][0]
        attempt=c.post(ep+'/attempts',headers=HEADERS,json={"student_id":task['student_ids'][0],"question_id":q['id'],
            "answer":q['answers'][0],"observed_on":"2026-09-30","submission_key":"live-feedback"}).json()
        started=time.monotonic()
        feedback=c.post(ep+f"/attempts/{attempt['id']}/feedback",headers=HEADERS,json={"session_id":session['id']})
        record('真实作答反馈',started,{"status_code":feedback.status_code,"result":feedback.json()},feedback.status_code==200 and feedback.json().get('correct')==attempt['correct'])
        started=time.monotonic()
        retest=c.post(base+'/practice/generate',headers=HEADERS,json={**request,'parent_id':item['id'],'scheduled_for':'2026-10-01'})
        record('真实同目标新题复测',started,{"status_code":retest.status_code,"result":retest.json()},retest.status_code==201)

    started=time.monotonic()
    reading=c.post(base+'/practice/reuse',headers=HEADERS,json={"objective":"用原文定位细节","student_ids":task['student_ids'],
        "question_ids":[w.questions[-1][4].id],"level":"basic","session_id":session['id']})
    record('真实阅读原文取舍',started,{"status_code":reading.status_code,"result":reading.json()},reading.status_code==201)
    results['failed_stages']=errors
    with w.app.state.session_factory() as db:
        results['usage'] = [{'run_id':run.id, 'capability':run.capability, 'status':run.status,
            'actual_tokens':run.actual_tokens, 'actual_cost_yuan':run.actual_cost_yuan,
            'model_calls':len(rows), 'input_tokens':sum(r.input_tokens for r in rows),
            'cache_read_tokens':sum(r.cache_read_tokens for r in rows),
            'output_tokens':sum(r.output_tokens for r in rows)}
            for run in db.scalars(select(AnalysisRun).where(AnalysisRun.session_id.in_([sid,session['id']])))
            for rows in [list(db.scalars(select(LlmUsageRecord).where(LlmUsageRecord.run_id==run.id)))]]
    save()
    assert not errors, f"Live scenario failures: {errors}; see {output}"
