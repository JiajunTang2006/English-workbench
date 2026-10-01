"""Teacher workflow, grading, original-source and provider-failure regression."""
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import (Term, Class, Student, Enrollment, Exam, ExamScore, ExamPaperVersion, ExamQuestion,
    StudentItemResult, AnalysisRun, Attachment)
from backend.app.agent.config import AgentConfig
from backend.app.agent.providers.fake_text import FakeTextProvider
from backend.app.agent.providers.base import ModelResponse, ModelUsage
from backend.app.services import practice

H = {"Authorization": f"Bearer {TOKEN}"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path))
    with app.state.session_factory() as db:
        term = db.scalar(select(Term).where(Term.status == "active"))
        cls = Class(term_id=term.id, name="合成一班")
        students = [Student(name=f"合成学生{i}", student_no=f"S{i:02}") for i in range(3)]
        db.add_all([cls, *students]); db.flush()
        for s in students: db.add(Enrollment(term_id=term.id, class_id=cls.id, student_id=s.id, status="active"))
        exam = Exam(term_id=term.id, name="合成卷", full_score=10); db.add(exam); db.flush()
        att = Attachment(term_id=term.id, title="合成 PDF", original_name="test.pdf", storage_name="test.pdf", mime_type="application/pdf", size_bytes=1, sha256="a"*64,
            metadata_json={"parsed": {"pages": [{"page": 1, "text": "A boy visited the park. He found a lost dog and returned it home."}]}})
        db.add(att); db.flush()
        paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed", source_attachment_ids_json=[att.id]); db.add(paper); db.flush()
        qs = [ExamQuestion(paper_version_id=paper.id, question_no="1", question_type="填空", content_text="He ___ to school.", max_score=5, correct_answer_json={"value": "goes"}, source_page=1),
              ExamQuestion(paper_version_id=paper.id, question_no="2", question_type="阅读", content_text="What did the boy find?", options_json={"A": "a lost dog", "B": "a cat"}, max_score=5, correct_answer_json={"value": "A"}, source_page=1)]
        db.add_all(qs); db.flush()
        for s in students:
            db.add(ExamScore(exam_id=exam.id, student_id=s.id, class_id_at_exam=cls.id, total_score=2, attendance_status="present"))
            for q in qs: db.add(StudentItemResult(exam_id=exam.id, student_id=s.id, question_id=q.id, score=1))
        db.commit(); ids = [s.id for s in students]; qids = [q.id for q in qs]; scope = dict(term_id=term.id, class_id=cls.id, exam_id=exam.id)
    cfg = AgentConfig(text_model_name="deepseek-chat", text_provider="deepseek", feature_flags={"agent_enabled": True, "text_agent_enabled": True})
    monkeypatch.setattr(practice, "get_agent_config", lambda: cfg)
    with TestClient(app) as c:
        t = c.post('/api/v1/teaching/tasks', headers=H, json={**scope, "title": "专项练习", "goal": "语言运用"}).json()
        base = f"/api/v1/teaching/tasks/{t['id']}"
        session = c.post(base + '/sessions', headers=H, json={}).json()
        yield c, base, ids, qids, session['id'], cfg


def draft(ids, **kw):
    return {"title": "分数练习", "objective": "等价分数", "student_ids": ids[:1], "level": "basic",
        "questions": [{"objective": "等价分数", "kind": "number", "prompt": "求 1/2 + 1/2", "answers": ["1"], "explanation": "同分母相加", "difficulty": "basic"}], **kw}


def adopt(c, base, data):
    res = c.post(base + '/practices', headers=H, json=data); assert res.status_code == 201, res.text
    p = res.json(); endpoint = base + f"/practices/{p['id']}"
    assert c.post(endpoint + '/confirm', headers=H, json={"checked_answers": True, "checked_scope": True}).status_code == 200
    return p, endpoint


def test_grade_idempotent_correction_and_immutable_revision(env):
    c, base, ids, _, _, _ = env
    p, ep = adopt(c, base, draft(ids))
    row = {"student_id": ids[0], "question_id": p['questions'][0]['id'], "answer": "2/2", "observed_on": "2026-09-29", "submission_key": "once", "hint_level": 1}
    a = c.post(ep + '/attempts', headers=H, json=row).json(); assert a['correct'] and not a['independent']
    assert c.post(ep + '/attempts', headers=H, json=row).json()['id'] == a['id']
    assert c.post(ep + '/attempts', headers=H, json={**row, "answer": "0"}).status_code == 409
    patch = {"expected_revision": 1, "correct": False, "reason": "教师核对发现代填"}
    fixed = c.patch(ep + f"/attempts/{a['id']}", headers=H, json=patch).json()
    assert not fixed['correct'] and fixed['graded_by'] == 'teacher' and fixed['corrections'][0]['correct']
    assert c.patch(ep + f"/attempts/{a['id']}", headers=H, json=patch).status_code == 409
    q = {k:v for k,v in p['questions'][0].items() if k not in {'id','position','version'}}
    q.update(prompt="求 1/3 + 2/3")
    revised = c.post(ep + f"/questions/{p['questions'][0]['id']}/revise", headers=H, json=q)
    assert revised.status_code == 201, revised.text
    assert revised.json()['status'] == 'draft' and revised.json()['id'] != p['id']
    assert c.get(ep, headers=H).json()['questions'][0]['prompt'] == "求 1/2 + 1/2"
    assert c.get(base, headers=H).json()['practice_progress']['objectives'][0]['hinted_correct'] == 0


def test_batch_atomic_scope_future_and_retest_novelty(env):
    c, base, ids, _, _, _ = env
    p, ep = adopt(c, base, draft(ids))
    row = {"student_id": ids[0], "question_id": p['questions'][0]['id'], "answer": "1", "observed_on": "2026-09-29", "submission_key": "one"}
    assert c.post(ep + '/attempts/batch', headers=H, json={"rows": [row, {**row, "student_id": ids[1], "submission_key": "two"}]}).status_code == 400
    assert c.get(ep, headers=H).json()['attempts'] == []
    assert c.post(ep + '/attempts', headers=H, json={**row, "observed_on": "2099-01-01"}).status_code == 400
    assert c.post(base + '/practices', headers=H, json=draft(ids, parent_id=p['id'])).status_code == 422
    retest = draft(ids, parent_id=p['id']); retest['questions'][0]['prompt'] = '求 2/3 + 1/3'
    rp, re = adopt(c, base, retest)
    assert c.post(re + '/attempts', headers=H, json={**row, "question_id": rp['questions'][0]['id']}).status_code == 201
    progress = c.get(base, headers=H).json()['practice_progress']; assert progress['mastery_status'] == 'pending_evidence'
    assert progress['objectives'][0]['retest_correct'] == 1
    copy = c.get(re + '?student_copy=true', headers=H).json()
    assert not any(k in copy['questions'][0] for k in ['answers','explanation','hints']) and 'attempts' not in copy


def fake(monkeypatch, body, usage=True):
    provider = FakeTextProvider([ModelResponse(content=json.dumps(body), usage=ModelUsage(100, 200) if usage else None)])
    if not usage:
        original = provider.complete
        async def complete(*args, **kwargs):
            response = await original(*args, **kwargs)
            response.usage = None
            return response
        provider.complete = complete
    monkeypatch.setattr(practice, '_create_text_provider', lambda cfg: provider)
    return provider


def test_ai_generation_and_feedback_are_audited_suggestions(env, monkeypatch):
    c, base, ids, _, session, _ = env
    body = draft(ids); provider = fake(monkeypatch, {k:v for k,v in body.items() if k in {'title','objective','questions'}})
    payload = {"objective": "等价分数", "student_ids": ids[:1], "level": "basic", "question_count": 1, "session_id": session}
    res = c.post(base + '/practice/generate', headers=H, json=payload); assert res.status_code == 201, res.text
    p = res.json(); assert len(provider.calls) == 1
    assert 'student_id' not in provider.calls[0]['messages'][1]['content']
    ep = base + f"/practices/{p['id']}"; c.post(ep + '/confirm', headers=H, json={"checked_answers": True, "checked_scope": True})
    a = c.post(ep + '/attempts', headers=H, json={"student_id":ids[0],"question_id":p['questions'][0]['id'],"answer":"1","observed_on":"2026-09-29","submission_key":"answer"}).json()
    fake(monkeypatch, {"suggested_correct": False, "feedback": "仅作建议", "next_step": "教师检查"}, usage=False)
    result = c.post(ep + f"/attempts/{a['id']}/feedback", headers=H, json={"session_id":session}); assert result.status_code == 200, result.text
    assert result.json()['correct'] is True and result.json()['feedback']['suggested_correct'] is False
    with c.app.state.session_factory() as db:
        run = db.get(AnalysisRun, result.json()['feedback']['run_id']); assert run.input_summary_json['usage_known'] is False


def test_failed_generation_never_saves_partial_practice(env, monkeypatch):
    c, base, ids, _, session, cfg = env
    provider = fake(monkeypatch, {"title":"无效","objective":"等价分数","questions":[]})
    payload = {"objective":"等价分数","student_ids":ids[:1],"level":"basic","question_count":1,"session_id":session}
    assert c.post(base + '/practice/generate', headers=H, json=payload).status_code == 422
    assert c.get(base, headers=H).json()['practices'] == []
    with c.app.state.session_factory() as db: assert db.scalar(select(AnalysisRun).where(AnalysisRun.session_id == session)).status == 'failed'
    object.__setattr__(cfg, "budget_soft_limit_yuan", .000001)
    res = c.post(base + '/practice/generate', headers=H, json=payload); assert res.status_code == 409
    assert len(provider.calls) == 1
    monkeypatch.setattr(practice, '_create_text_provider', lambda cfg: (_ for _ in []).throw(RuntimeError('factory error')))
    res = c.post(base + '/practice/generate', headers=H, json={**payload,"confirmed_budget_yuan":1}); assert res.status_code == 502
    with c.app.state.session_factory() as db:
        assert db.scalar(select(AnalysisRun).order_by(AnalysisRun.id.desc())).status == 'failed'


def test_original_question_reading_verification_and_plugin_switch(env, monkeypatch):
    c, base, ids, qids, session, _ = env
    pool = c.get(base + '/practice/sources', headers=H).json(); assert len(pool['questions']) == 2
    payload = {"objective":"语言运用","student_ids":ids[:1],"question_ids":qids[:1],"level":"basic","session_id":session}
    res = c.post(base + '/practice/reuse', headers=H, json=payload); assert res.status_code == 201, res.text
    p = res.json(); assert p['questions'][0]['source']['question_id'] == qids[0]
    ep = base + f"/practices/{p['id']}"; c.post(ep + '/confirm', headers=H, json={"checked_answers":True,"checked_scope":True})
    a = c.post(ep + '/attempts', headers=H, json={"student_id":ids[0],"question_id":p['questions'][0]['id'],"answer":"goes","observed_on":"2026-09-29","submission_key":"reuse"}).json()
    assert a['new_question'] is False and a['correct'] is None
    fake(monkeypatch, {"decisions":[{"source_ref":f"question_{qids[1]}","mode":"excerpt","passage":"He found a lost dog and returned it home.","reason":"细节定位，只需相关句"}]})
    result = c.post(base + '/practice/reuse', headers=H, json={**payload,"question_ids":qids[1:]}); assert result.status_code == 201, result.text
    assert result.json()['questions'][0]['passage'].startswith('He found')
    fake(monkeypatch, {"decisions":[{"source_ref":f"question_{qids[1]}","mode":"full","passage":"invented article","reason":"主旨"}]})
    assert c.post(base + '/practice/reuse', headers=H, json={**payload,"question_ids":qids[1:]}).status_code == 422
    count = len(c.get(base, headers=H).json()['practices']); assert count == 2
    c.post('/api/v1/plugins/targeted_practice/disable',headers=H)
    assert c.post(base + '/practice/reuse', headers=H, json=payload).status_code == 409
    assert c.get(ep,headers=H).status_code == 200


def test_ai_interrupted_by_teacher_revision_and_invalid_null_output(env, monkeypatch):
    c, base, ids, _, session, cfg = env
    from backend.app.models.teaching_entities import TeachingTask
    async def changed(*args, **kwargs):
        with c.app.state.session_factory() as db:
            task = db.get(TeachingTask, int(base.rsplit('/',1)[1])); task.revision += 1; task.goal = '教师更新目标'; db.commit()
        return ModelResponse(content=json.dumps({k:v for k,v in draft(ids).items() if k in {'title','objective','questions'}}), usage=ModelUsage(100, 100))
    provider = FakeTextProvider([]); provider.complete = changed
    monkeypatch.setattr(practice, '_create_text_provider', lambda cfg: provider)
    payload = {"objective":"等价分数","student_ids":ids[:1],"level":"basic","question_count":1,"session_id":session}
    assert c.post(base + '/practice/generate', headers=H, json=payload).status_code == 422
    assert c.get(base, headers=H).json()['practices'] == []
    fake(monkeypatch, {"title":"bad","objective":"等价分数","questions":None})
    assert c.post(base + '/practice/generate', headers=H, json=payload).status_code == 422
    with c.app.state.session_factory() as db:
        assert all(r.status == 'failed' for r in db.scalars(select(AnalysisRun)))


def test_group_task_targets_and_practice_query_are_scoped(env):
    c, base, ids, _, session, _ = env
    res = c.patch(base,headers=H,json={"expected_revision":1,"target_type":"group","student_ids":ids[:2]})
    assert res.status_code == 200
    assert c.post(base + '/practices', headers=H, json=draft(ids[2:])).status_code == 400
    p, ep = adopt(c, base, draft(ids))
    row = {"student_id":ids[0],"question_id":p['questions'][0]['id'],"answer":"1","observed_on":"2026-09-29","submission_key":"query"}
    assert c.post(ep+'/attempts',headers=H,json=row).status_code == 201
    from backend.app.agent.education_bridge.bridge_cli import run_tool
    from backend.app.services.agent_analysis.conversation import run_mapper
    with c.app.state.session_factory() as db:
        from backend.app.models.teaching_entities import TeachingTask
        task = db.get(TeachingTask, int(base.rsplit('/',1)[1])); scope=dict(term_id=task.term_id,class_id=task.class_id,exam_id=task.exam_id)
        mapper = run_mapper(db, scope)
        run = AnalysisRun(session_id=session,capability='general_chat',status='running',term_id=task.term_id,class_id=task.class_id,
            input_summary_json={'identity_snapshot':mapper.snapshot()})
        db.add(run); db.commit(); scope['run_id']=run.id
        result = run_tool('get_practice_context',db,scope,{'student_ref':mapper.to_anonymous(ids[0])})
        assert result['data']['objective_progress'][0]['independent_new_correct'] == 1
        other = run_tool('get_practice_context',db,scope,{'student_ref':mapper.to_anonymous(ids[1])})
        assert other['data']['objective_progress'] == []


def test_card_attempt_summary_is_bound_to_each_practice_and_updates_after_review(env):
    c, base, ids, _, _, _ = env
    content = draft(ids)
    content['questions'][0].update(kind='text', prompt='解释分数等价的依据', answers=['分子分母同比例变化'])
    first, first_ep = adopt(c, base, content)
    second, second_ep = adopt(c, base, content)
    assert c.get(first_ep, headers=H).json()['attempt_summary'] == {'total': 0, 'pending': 0}
    answer = c.post(first_ep + '/attempts', headers=H, json={
        'student_id': ids[0], 'question_id': first['questions'][0]['id'],
        'answer': '分子分母同比例变化', 'observed_on': '2026-09-29', 'submission_key': 'card-summary',
    }).json()
    summaries = {p['id']: p['attempt_summary'] for p in c.get(base, headers=H).json()['practices']}
    assert summaries[first['id']] == {'total': 1, 'pending': 1}
    assert summaries[second['id']] == {'total': 0, 'pending': 0}
    result = c.patch(first_ep + '/attempts/' + str(answer['id']), headers=H, json={
        'expected_revision': answer['revision'], 'correct': True, 'reason': '依据完整',
    })
    assert result.status_code == 200
    assert c.get(first_ep, headers=H).json()['attempt_summary'] == {'total': 1, 'pending': 0}
    assert 'attempt_summary' not in c.get(first_ep + '?student_copy=true', headers=H).json()
