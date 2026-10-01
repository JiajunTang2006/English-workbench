"""Continuous teacher journeys over synthetic data; these tests do not judge an LLM."""
import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import (
    AgentSession, AnalysisRun, Attachment, Class, Enrollment, Exam, ExamPaperVersion,
    ExamQuestion, ExamScore, Student, StudentItemResult, Term,
)
from backend.app.agent.config import AgentConfig
from backend.app.agent.providers.base import ModelResponse, ModelUsage
from backend.app.agent.providers.fake_text import FakeTextProvider
from backend.app.agent.education_bridge.bridge_cli import run_tool
from backend.app.services.agent_analysis.conversation import prepare_turn, run_mapper
from backend.app.services import practice

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PASSAGE = "Lily walked to the library. She borrowed a book about plants. At home, she read it with her brother."


@pytest.fixture
def classroom(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path))
    with app.state.session_factory() as db:
        term = db.scalar(select(Term).where(Term.status == "active"))
        classes = [Class(term_id=term.id, name="八1班"), Class(term_id=term.id, name="八2班")]
        db.add_all(classes); db.flush()
        students = []
        totals = {}
        for ci, cls in enumerate(classes):
            for i in range(60):
                name = ["张晨", "李雨桐", "张晨曦", "陈宇"][i] if i < 4 else f"模拟同学{ci}-{i:02}"
                student = Student(name=name, student_no=f"SIM{ci}{i:03}", parent_phone="13800138000")
                db.add(student); db.flush(); students.append(student)
                db.add(Enrollment(term_id=term.id, class_id=cls.id, student_id=student.id, status="active"))
        exams = []
        questions = []
        attachment = Attachment(term_id=term.id, title="模拟月考试卷", original_name="synthetic.pdf",
            storage_name="synthetic.pdf", mime_type="application/pdf", size_bytes=1, sha256="a" * 64,
            metadata_json={"parsed": {"pages": [{"page": 1, "text": PASSAGE}]}})
        db.add(attachment); db.flush()
        for ei, day in enumerate([10, 20, 29]):
            exam = Exam(term_id=term.id, name=f"模拟第{ei + 1}次测验", full_score=20, exam_date=date(2026, 9, day))
            db.add(exam); db.flush(); exams.append(exam)
            paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed", source_attachment_ids_json=[attachment.id])
            db.add(paper); db.flush()
            qs = [ExamQuestion(paper_version_id=paper.id, question_no=str(qi + 1), question_type="阅读" if qi >= 4 else "填空",
                section_name="阅读定位" if qi >= 4 else "语言运用", max_score=2.5, source_page=1,
                content_text="What did Lily borrow?" if qi >= 4 else f"Item {qi + 1}: He ___ to school.",
                options_json={"A": "a book", "B": "a ball"} if qi >= 4 else {}, correct_answer_json={"value": "A" if qi >= 4 else "goes"})
                for qi in range(8)]
            db.add_all(qs); db.flush(); questions.append(qs)
            for index, student in enumerate(students):
                ci, local = divmod(index, 60)
                absent = local == 59
                scores = [0.5 if qi >= 4 and local < 3 else float(1 + (local + ei) % 2) for qi in range(8)]
                if ci == 1 and local == 0:
                    scores[4] = 1.5  # Homonyms must have different facts to expose object mix-ups.
                total = None if absent else sum(scores)
                totals[(exam.id, student.id)] = total
                db.add(ExamScore(exam_id=exam.id, student_id=student.id, class_id_at_exam=classes[ci].id,
                    total_score=total, attendance_status="absent" if absent else "present"))
                if not absent:
                    for qi, q in enumerate(qs):
                        # One missing item in the current exam must remain missing, not become zero.
                        if ei == 2 and local == 3 and qi == 7:
                            continue
                        db.add(StudentItemResult(exam_id=exam.id, student_id=student.id, question_id=q.id, score=scores[qi]))
        chat = AgentSession(term_id=term.id, subject_key="english", title="模拟教师对话")
        db.add(chat); db.commit()
        world = SimpleNamespace(app=app, db=db, term=term, classes=classes, students=students,
            exams=exams, questions=questions, totals=totals, chat=chat)
        cfg = AgentConfig(text_model_name="deepseek-chat", feature_flags={"agent_enabled": True, "text_agent_enabled": True})
        monkeypatch.setattr(practice, "get_agent_config", lambda: cfg)
        with TestClient(app) as client:
            world.client = client
            yield world


def replay(world, message, *, refs=None, aliases=None, scope=None, mode=None):
    scope = scope or {"term_id": world.term.id}
    state, snapshot, context, focus = prepare_turn(world.db, world.chat.id, scope, message, refs, aliases, mode)
    run = AnalysisRun(session_id=world.chat.id, term_id=world.term.id, capability="general_chat", status="completed",
        input_summary_json={"conversation_state": state, "identity_snapshot": snapshot})
    world.db.add(run); world.db.commit()
    print(json.dumps({"teacher": message, "status": context["resolution_status"],
        "people": context["people"], "unknown": context["unresolved_queries"]}, ensure_ascii=False))
    return state, context, focus, run


def test_teacher_changes_people_and_keeps_corrections(classroom):
    w = classroom
    _, context, focus, _ = replay(w, "看看张晨这次的表现")
    assert not focus and context["resolution_status"] == "ambiguous"
    ref = context["candidates"][0]["student_ref"]
    assert replay(w, "是八1班这位", refs=[ref])[2] == [w.students[0].id]
    assert replay(w, "后面叫他小晨", aliases={"小晨": ref})[2] == [w.students[0].id]
    assert replay(w, "推断这部分还没教，先只看定位题")[2] == [w.students[0].id]
    assert replay(w, "八1班李雨桐呢")[2] == [w.students[1].id]
    assert set(replay(w, "和八1班小晨比较一下")[2]) == {w.students[0].id, w.students[1].id}
    assert "还没教" in str(replay(w, "继续，但不要额外作业")[0]["teacher_corrections"])
    assert replay(w, "全班阅读题表现呢")[2] == []
    assert replay(w, "再看八1班张晨曦")[2] == [w.students[2].id]


def test_two_qualified_homonyms_can_be_compared(classroom):
    w = classroom
    replay(w, "八1班李雨桐呢")
    _, context, focus, _ = replay(w, "把八1班张晨和八2班张晨比较一下")
    assert context["resolution_status"] == "matched"
    assert set(focus) == {w.students[0].id, w.students[60].id}


@pytest.mark.parametrize("message", ["给赵磊出三道阅读题", "为赵磊设计专项练习", "看看赵磊同学的表现", "给八1班赵磊出三道阅读题"])
def test_unknown_named_student_does_not_reuse_last_student(classroom, message):
    w = classroom
    replay(w, "八1班李雨桐呢")
    _, context, focus, _ = replay(w, message)
    assert not focus and context["resolution_status"] == "not_found"
    assert "赵磊" in context["unresolved_queries"]


@pytest.mark.parametrize("message", ["赵磊呢", "那赵磊怎么样？"])
def test_unknown_name_followup_does_not_reuse_last_student(classroom, message):
    replay(classroom, "八1班李雨桐呢")
    _, context, focus, _ = replay(classroom, message)
    assert not focus and context["unresolved_queries"] == ["赵磊"]


def test_two_similar_known_names_in_one_sentence(classroom):
    assert set(replay(classroom, "把八1班张晨和八1班张晨曦比较")[2]) == {classroom.students[0].id, classroom.students[2].id}


def test_unknown_longer_name_is_not_a_known_shorter_name(classroom):
    _, context, focus, _ = replay(classroom, "给张晨光出三道题")
    assert not focus and context["resolution_status"] == "not_found"
    assert context["unresolved_queries"] == ["张晨光"]


@pytest.mark.parametrize("message", ["给我出三道阅读题", "为全班设计专项练习", "看看阅读理解的表现"])
def test_teaching_topic_is_not_reported_as_unknown_student(classroom, message):
    _, context, _, _ = replay(classroom, message)
    assert context["resolution_status"] != "not_found"
    assert context["unresolved_queries"] == []


def test_student_ref_is_not_a_prefix_match_in_large_roster(classroom):
    w = classroom
    mapper = run_mapper(w.db, {"term_id": w.term.id})
    ref = mapper.to_anonymous(w.students[99].id)
    assert replay(w, f"看看 {ref} 的表现")[2] == [w.students[99].id]


def test_live_score_queries_keep_class_absence_and_cache_consistent(classroom):
    w = classroom
    scope = {"term_id": w.term.id, "class_id": w.classes[0].id, "exam_id": w.exams[-1].id}
    _, _, _, run = replay(w, "看看全班成绩", scope=scope)
    scope["run_id"] = run.id
    first = run_tool("get_exam_overview", w.db, scope, {})["data"]["data"]
    valid = [w.totals[(w.exams[-1].id, s.id)] for s in w.students[:60] if w.totals[(w.exams[-1].id, s.id)] is not None]
    assert first["participant_count"] == 59
    assert first["average_score"] == pytest.approx(sum(valid) / len(valid), abs=0.01)
    run_tool("get_exam_overview", w.db, scope, {})
    w.db.refresh(run); assert run.input_summary_json["query_stats"]["cache_hits"] == 1
    score = w.db.scalar(select(ExamScore).where(ExamScore.exam_id == w.exams[-1].id, ExamScore.student_id == w.students[0].id))
    score.total_score = 20; w.db.commit()
    third = run_tool("get_exam_overview", w.db, scope, {})["data"]["data"]
    assert third["average_score"] != first["average_score"]
    w.db.refresh(run); assert run.input_summary_json["query_stats"]["cache_hits"] == 1


def test_teacher_practice_feedback_and_retest_journey(classroom, monkeypatch):
    w = classroom; c = w.client
    task = c.post('/api/v1/teaching/tasks', headers=HEADERS, json={"term_id":w.term.id,"class_id":w.classes[0].id,
        "exam_id":w.exams[-1].id,"title":"模拟课内阅读补救","goal":"用原文定位细节",
        "constraints":{"lesson_minutes":20,"no_homework":True,"taught_content":"已教细节定位，尚未教推断"}})
    assert task.status_code == 201, task.text
    base = f"/api/v1/teaching/tasks/{task.json()['id']}"
    chat = c.post(base+'/sessions',headers=HEADERS,json={}).json()
    weak = c.get(base+'/practice/weaknesses',headers=HEADERS).json()
    first = next(s for s in weak['students'] if s['student_id']==w.students[0].id)
    assert any(s['section_name']=='阅读定位' for s in first['weak_sections'])
    missing = next(s for s in weak['students'] if s['student_id']==w.students[3].id)
    assert missing['missing_items']==1
    c.post(base+'/feedback',headers=HEADERS,json={"kind":"correction","note":"推断尚未教，不据此判断能力差","correction_reason":"not_taught"})
    body={"title":"细节定位专项练习","objective":"用原文定位细节","questions":[{"objective":"用原文定位细节","kind":"choice",
        "prompt":"What did Lily borrow?","passage":PASSAGE,"options":{"A":"a book","B":"a ball"},"answers":["A"],
        "explanation":"She borrowed a book about plants.","difficulty":"basic"}]}
    provider=FakeTextProvider([ModelResponse(content=json.dumps(body),usage=ModelUsage(1200,300))])
    monkeypatch.setattr(practice,'_create_text_provider',lambda cfg:provider)
    request={"objective":"用原文定位细节","student_ids":[w.students[0].id,w.students[1].id],"question_count":1,"level":"basic","session_id":chat['id']}
    generated=c.post(base+'/practice/generate',headers=HEADERS,json=request)
    assert generated.status_code==201,generated.text
    payload=json.loads(provider.calls[0]['messages'][1]['content'])
    assert len(payload['context']['students'])==2
    assert payload['subject']=={'key':'english','label':'英语'}
    assert '推断尚未教' in str(payload['context']['teacher_corrections'])
    assert '13800138000' not in str(provider.calls) and 'SIM0000' not in str(provider.calls)
    item=generated.json();ep=base+f"/practices/{item['id']}"
    rows=[{"student_id":sid,"question_id":item['questions'][0]['id'],"answer":"A","observed_on":"2026-09-29",
        "submission_key":f"classroom-{i}","hint_level":i} for i,sid in enumerate(request['student_ids'])]
    assert c.post(ep+'/attempts/batch',headers=HEADERS,json={'rows':rows}).status_code==409
    assert c.post(ep+'/confirm',headers=HEADERS,json={'checked_answers':True,'checked_scope':True}).status_code==200
    attempts=c.post(ep+'/attempts/batch',headers=HEADERS,json={'rows':rows}).json()
    assert attempts[0]['independent'] and not attempts[1]['independent']
    assert c.post(ep+'/attempts/batch',headers=HEADERS,json={'rows':rows}).json()==attempts
    copy=c.get(ep+'?student_copy=true',headers=HEADERS).json()
    assert copy['questions'][0]['passage']==PASSAGE
    assert not any(k in copy['questions'][0] for k in ('answers','explanation','hints'))
    changed=c.patch(ep+f"/attempts/{attempts[0]['id']}",headers=HEADERS,json={"expected_revision":1,"correct":False,"reason":"教师核对为同伴代填"}).json()
    assert changed['corrections'][0]['correct'] is True and changed['correct'] is False
    body['questions'][0]['prompt']='Who read the book with Lily?'
    body['questions'][0]['options']={'A':'her brother','B':'her teacher'}
    provider=FakeTextProvider([ModelResponse(content=json.dumps(body),usage=ModelUsage(1500,300))])
    retest=c.post(base+'/practice/generate',headers=HEADERS,json={**request,'parent_id':item['id'],'scheduled_for':'2026-09-30'})
    assert retest.status_code==201,retest.text
    retest_input=json.loads(provider.calls[0]['messages'][1]['content'])
    assert retest_input['previous_questions']==['What did Lily borrow?']
    new=retest.json();new_ep=base+f"/practices/{new['id']}"
    c.post(new_ep+'/confirm',headers=HEADERS,json={'checked_answers':True,'checked_scope':True})
    for i,sid in enumerate(request['student_ids']):
        res=c.post(new_ep+'/attempts',headers=HEADERS,json={**rows[i],'question_id':new['questions'][0]['id'],'hint_level':0,'observed_on':'2026-09-30'})
        assert res.status_code==201,res.text
    progress=c.get(base,headers=HEADERS).json()['practice_progress']
    assert progress['mastery_status']=='pending_evidence'
    assert sum(r['retest_correct'] for r in progress['objectives'])==2
    resumed=c.post(base+'/sessions',headers=HEADERS,json={}).json()
    assert resumed['id']!=chat['id']
    assert len(c.get(base,headers=HEADERS).json()['practices'])==2
    print('Teacher journey: weaknesses → correction → generate → adopt → student copy → hinted answers → teacher correction → retest → resume: passed')
