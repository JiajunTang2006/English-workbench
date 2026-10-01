"""Natural teacher chat: real DB evidence without a teaching task or selected exam."""
from datetime import date
import pytest
from sqlalchemy import select
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import (Term, Class, Student, Enrollment, Exam, ExamScore,
    ExamPaperVersion, ExamQuestion, StudentItemResult, StudentProfile, Attachment, AgentSession, AnalysisRun)
from backend.app.services.agent_analysis.conversation import run_mapper
from backend.app.services.student_learning import learning_evidence, original_question

@pytest.fixture
def world(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with app.state.session_factory() as db:
        term = db.scalar(select(Term).where(Term.status == 'active'))
        cls = Class(term_id=term.id, name='测试班'); other = Class(term_id=term.id, name='其他班')
        students = [Student(name='小明', student_no='TEST-LEARNING-1'), Student(name='小红', student_no='TEST-LEARNING-2')]
        db.add_all([cls, other, *students]); db.flush()
        for s,c in zip(students,[cls,other]):
            db.add(Enrollment(term_id=term.id, class_id=c.id, student_id=s.id, status='active'))
        att = Attachment(term_id=term.id, title='阅读来源', original_name='source.pdf', storage_name='source.pdf',
            mime_type='application/pdf', size_bytes=1, sha256='a'*64,
            metadata_json={'parsed': {'pages': [{'page':1,'text':'A boy found a lost dog.'}]}})
        db.add(att); db.flush()
        exam = Exam(term_id=term.id, name='最近考试', exam_date=date(2026,9,30), full_score=10)
        db.add(exam); db.flush()
        paper = ExamPaperVersion(exam_id=exam.id, version=1, status='confirmed', source_attachment_ids_json=[att.id])
        db.add(paper); db.flush()
        questions = [ExamQuestion(paper_version_id=paper.id, question_no='1', max_score=5, question_type='填空',
            content_text='He ___ to school.', correct_answer_json={'value':'goes'}, knowledge_nodes_json=['一般现在时']),
            ExamQuestion(paper_version_id=paper.id, question_no='2', max_score=5, question_type='阅读',
            content_text='What did the boy find?', correct_answer_json={'value':'a lost dog'}, source_page=1)]
        db.add_all(questions); db.flush()
        db.add(ExamScore(exam_id=exam.id, student_id=students[0].id, class_id_at_exam=cls.id, total_score=3))
        db.add(StudentItemResult(exam_id=exam.id, student_id=students[0].id, question_id=questions[0].id, score=1))
        db.add(StudentProfile(term_id=term.id, student_id=students[0].id, version=1,
            profile_json={'subject_key':'english','summary':'定位证据需要支持','weaknesses':['时态选择']}))
        conversation = AgentSession(term_id=term.id, subject_key='english', title='普通对话')
        db.add(conversation); db.commit()
        scope = {'term_id':term.id,'class_id':cls.id,'student_id':students[0].id}
        yield db, scope, run_mapper(db,scope), questions, students, conversation, att


def test_recent_scores_profile_and_missing_items_without_exam_or_task(world):
    db, scope, mapper, qs, _, _, _ = world
    result = learning_evidence(db,scope,mapper)
    assert result['confirmed_profile']['weaknesses'] == ['时态选择']
    exam = result['recent_exams'][0]
    assert exam['detail_status'] == 'partial' and exam['scored_items'] == 1
    assert exam['sections'][1]['scored_items'] == 0 and exam['sections'][1]['max_score'] == 5
    assert exam['wrong_questions'][0]['question_ref'] == f'question_{qs[0].id}'
    original = original_question(db,scope,mapper,f'question_{qs[0].id}')
    assert original['prompt'] == 'He ___ to school.' and original['confirmed_answer']['value'] == 'goes'
    assert original['source_pages'] == []


def test_reading_requires_actual_loss_and_reports_source_completeness(world):
    db, scope, mapper, qs, students, _, att = world
    with pytest.raises(ValueError, match='失分索引'):
        original_question(db,scope,mapper,f'question_{qs[1].id}')
    db.add(StudentItemResult(exam_id=qs[1].paper_version.exam_id,student_id=students[0].id,question_id=qs[1].id,score=1));db.commit()
    result = original_question(db,scope,mapper,f'question_{qs[1].id}')
    assert result['reading'] and result['source_status'] == 'available_pages'
    assert result['source_pages'][0]['text'] == 'A boy found a lost dog.'
    att.metadata_json = {'parsed': {'pages': []}};db.commit()
    assert original_question(db,scope,mapper,f'question_{qs[1].id}')['source_status'] == 'no_extracted_passage'


def test_cross_class_refs_and_invalid_parameters_rejected(world):
    db, scope, mapper, qs, students, _, _ = world
    wider = run_mapper(db,{'term_id':scope['term_id']})
    with pytest.raises(ValueError,match='授权范围'):
        learning_evidence(db,scope,wider,wider.to_anonymous(students[1].id))
    with pytest.raises(ValueError): learning_evidence(db,scope,mapper,horizon=99)
    with pytest.raises(ValueError): original_question(db,scope,mapper,'anything')
    with pytest.raises(ValueError,match='确认学生'):
        learning_evidence(db,{**scope,'student_id':None},mapper)


def test_both_runtime_tools_and_optional_skill_context(world):
    from backend.app.agent.registry.capabilities import GENERAL_CHAT_READ_TOOLS
    from backend.app.agent.tools.dialogue_tools import SCHEMAS
    from backend.app.agent.education_bridge.bridge_cli import run_tool, _TOOL_ARG_SCHEMA
    from backend.app.services.agent_analysis.sessions import SessionService
    from backend.app.services.practice import practice_skill
    db, scope, mapper, _, _, conversation, _ = world
    for name in ['get_student_learning_evidence','get_original_question']:
        assert name in GENERAL_CHAT_READ_TOOLS and name in SCHEMAS and name in _TOOL_ARG_SCHEMA
    result = run_tool('get_student_learning_evidence',db,scope,{})
    assert result['data']['recent_exams'][0]['exam_name'] == '最近考试'
    run = AnalysisRun(session_id=conversation.id,term_id=scope['term_id'],capability='general_chat',status='queued',
        input_summary_json={'selected_plugin_id':'targeted_practice','plugin_skill':practice_skill()})
    db.add(run);db.commit()
    messages = SessionService(db).build_multi_turn_messages(conversation.id,'系统','出三道练习',current_run_id=run.id,include_formal_context=False)
    assert '【教师选用：专项推题】' in messages[0]['content']
    assert '无需创建教学任务' in messages[0]['content']
    run.input_summary_json = {};db.commit()
    messages = SessionService(db).build_multi_turn_messages(conversation.id,'系统','查看弱项',current_run_id=run.id,include_formal_context=False)
    assert '【教师选用：专项推题】' not in messages[0]['content']


def test_other_subject_profile_and_stale_paper_not_used(world):
    db,scope,mapper,qs,students,_,_ = world
    profile = db.scalar(select(StudentProfile).where(StudentProfile.student_id==students[0].id))
    profile.profile_json={'subject_key':'math','weaknesses':['分数运算']};db.commit()
    assert learning_evidence(db,scope,mapper)['confirmed_profile'] is None
    db.add(ExamPaperVersion(exam_id=qs[0].paper_version.exam_id,version=2,status='confirmed'));db.commit()
    assert learning_evidence(db,scope,mapper)['recent_exams'][0]['wrong_questions']==[]
    with pytest.raises(ValueError,match='失分索引'):original_question(db,scope,mapper,f'question_{qs[0].id}')


def test_model_can_choose_evidence_then_original_in_natural_chat(world):
    import asyncio,json
    from backend.app.agent.config import AgentConfig
    from backend.app.agent.providers.fake_text import FakeTextProvider
    from backend.app.agent.providers.base import ModelResponse, ToolCall
    from backend.app.agent.orchestrator import AgentOrchestrator, OrchestratorRequest
    from backend.app.agent.registry.tools import create_default_registry
    from backend.app.agent.registry.capabilities import create_default_capability_registry
    from backend.app.services.practice import practice_skill
    db,scope,mapper,qs,_,conversation,_ = world
    run = AnalysisRun(session_id=conversation.id,term_id=scope['term_id'],capability='general_chat',status='queued',
        input_summary_json={'identity_snapshot':mapper.snapshot(),'plugin_skill':practice_skill()})
    db.add(run);db.commit()
    def after_evidence(request):
        tool_result=json.loads(request['messages'][-1]['content'])['data']
        assert tool_result['recent_exams'][0]['detail_status']=='partial'
        assert tool_result['confirmed_profile']['weaknesses']==['时态选择']
        ref=tool_result['recent_exams'][0]['wrong_questions'][0]['question_ref']
        return ModelResponse(tool_calls=[ToolCall('get_original_question',{'question_ref':ref})])
    def after_original(request):
        tool_result=json.loads(request['messages'][-1]['content'])['data']
        assert tool_result['prompt']=='He ___ to school.'
        assert tool_result['confirmed_answer']['value']=='goes'
        return ModelResponse(content='最近考试第1题得分1/5，画像提示时态选择需要练习。原题：He ___ to school. 答案：goes。阅读小分缺失，暂不判断阅读弱项。')
    provider=FakeTextProvider([ModelResponse(tool_calls=[ToolCall('get_student_learning_evidence',{})]),after_evidence,after_original])
    config=AgentConfig(text_model_name='fake-model',feature_flags={'agent_enabled':True,'text_agent_enabled':True})
    agent=AgentOrchestrator(config=config,text_provider=provider,tool_registry=create_default_registry(),capability_registry=create_default_capability_registry())
    async def journey():
        try:
            return await agent.run(OrchestratorRequest(teacher_id=1,capability_name='general_chat',
                scope={**scope,'run_id':run.id},user_message='看看小明近期弱项并找一道原错题',db_session_id=conversation.id,confirmed_budget_yuan=1),db_session=db)
        finally: await agent.close()
    result=asyncio.run(journey())
    assert result.success and '1/5' in result.answer and '阅读小分缺失' in result.answer
    assert len(provider.calls)==3
    assert '【教师选用：专项推题】' in provider.calls[0]['messages'][0]['content']
    assert all('TEST-LEARNING-1' not in str(c['messages']) for c in provider.calls)


def test_message_api_freezes_selected_skill_and_disabled_plugin_writes_nothing(world,monkeypatch):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from backend.app.auth import TOKEN
    from backend.app.agent.config import AgentConfig
    from backend.app.routers import agent as route
    db,scope,_,_,students,conversation,_ = world
    app=create_app(Settings(data_dir=Path(db.bind.url.database).parent))
    config=AgentConfig(text_model_name='deepseek-chat',feature_flags={'agent_enabled':True,'text_agent_enabled':True})
    monkeypatch.setattr(route,'get_agent_config',lambda:config)
    async def no_paid_run(*args,**kwargs):pass
    monkeypatch.setattr(route,'schedule_analysis_run',no_paid_run)
    headers={'Authorization':f'Bearer {TOKEN}'}
    with TestClient(app) as client:
        result=client.post(f'/api/v1/agent/sessions/{conversation.id}/messages',headers=headers,
            json={'content':'查看小明近期薄弱点','plugin_id':'targeted_practice'})
        assert result.status_code==202,result.text
        assert result.json()['capability']=='general_chat'
        db.expire_all();run=db.get(AnalysisRun,result.json()['run_id'])
        assert run.exam_id is None and run.student_id==students[0].id
        assert run.input_summary_json['selected_plugin_id']=='targeted_practice'
        assert 'get_student_learning_evidence' in run.input_summary_json['plugin_skill']
        assert run.input_summary_json['teaching_task_id'] is None
        assert client.post('/api/v1/plugins/targeted_practice/disable',headers=headers).status_code==200
        count=len(list(db.scalars(select(AnalysisRun))))
        result=client.post(f'/api/v1/agent/sessions/{conversation.id}/messages',headers=headers,
            json={'content':'继续出三道练习','plugin_id':'targeted_practice'})
        assert result.status_code==409 and result.json()['detail']['code']=='PLUGIN_DISABLED'
        db.expire_all();assert len(list(db.scalars(select(AnalysisRun))))==count


def test_text_only_provider_receives_evidence_without_faking_tool_calls(world):
    import asyncio,json
    from backend.app.agent.config import AgentConfig
    from backend.app.agent.providers.fake_text import FakeTextProvider
    from backend.app.agent.providers.base import ModelCapabilities,ModelResponse
    from backend.app.agent.orchestrator import AgentOrchestrator,OrchestratorRequest
    from backend.app.agent.registry.tools import create_default_registry
    from backend.app.agent.registry.capabilities import create_default_capability_registry
    db,scope,mapper,_,_,conversation,_ = world
    run=AnalysisRun(session_id=conversation.id,term_id=scope['term_id'],capability='general_chat',status='queued',input_summary_json={'identity_snapshot':mapper.snapshot()})
    db.add(run);db.commit()
    def answer(request):
        prompt='\n'.join(m['content'] for m in request['messages'])
        assert '本地预查学习证据' in prompt and '最近考试' in prompt and '时态选择' in prompt
        assert request['tools']==[]
        return ModelResponse(content='根据已提供小分，第1题失分；阅读小分尚未录入。')
    provider=FakeTextProvider([answer],capabilities=ModelCapabilities(supports_tool_calls=False))
    agent=AgentOrchestrator(config=AgentConfig(text_model_name='fake-model'),text_provider=provider,
        tool_registry=create_default_registry(),capability_registry=create_default_capability_registry())
    async def journey():
        try:return await agent.run(OrchestratorRequest(teacher_id=1,capability_name='general_chat',scope={**scope,'run_id':run.id},user_message='查看小明近期薄弱点',db_session_id=conversation.id,confirmed_budget_yuan=1),db_session=db)
        finally:await agent.close()
    result=asyncio.run(journey());assert result.success and '尚未录入' in result.answer


def test_unconfirmed_name_candidate_cannot_be_used_for_learning_query(world):
    db,scope,mapper,_,_,conversation,_=world
    ref=mapper.to_anonymous(scope['student_id'])
    run=AnalysisRun(session_id=conversation.id,term_id=scope['term_id'],capability='general_chat',status='queued',
        input_summary_json={'conversation_state':{'resolution_status':'ambiguous','candidate_refs':[ref],'focus_refs':[]}})
    db.add(run);db.commit()
    with pytest.raises(ValueError,match='候选尚未确认'):
        learning_evidence(db,{**scope,'student_id':None,'run_id':run.id},mapper,student_ref=ref)
