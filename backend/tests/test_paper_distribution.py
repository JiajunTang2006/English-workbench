"""Teacher ranges → stored paper → untyped item scores → deterministic chart."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import AppSetting, ExamPaperVersion, ExamQuestion, StudentItemResult
from backend.app.services.student_score_details import get_student_score_details

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
DEFAULT_URL = '/api/v1/exams/paper-distribution/default'

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as c:
        yield c


def setup_exam(c, name='月考'):
    classroom = c.post('/api/v1/classes', headers=HEADERS, json={'name': '711'}).json()
    student = c.post('/api/v1/students', headers=HEADERS, json={
        'student_no': '202601', 'name': '测试学生', 'class_id': classroom['id']}).json()
    exam = c.post('/api/v1/exams', headers=HEADERS, json={'name': name}).json()
    c.put(f"/api/v1/exams/{exam['id']}/scores", headers=HEADERS,
          json={'items': [{'student_id': student['id'], 'total_score': 80}]})
    return exam, student


def write_default(c, rows, revision=0):
    return c.put(DEFAULT_URL, headers=HEADERS, json={'subject_key': 'english',
        'rows': rows, 'expected_revision': revision})


def row(kind, numbers):
    return {'question_type': kind, 'question_numbers': numbers}


def configure(c, exam_id, rows):
    url = f'/api/v1/exams/{exam_id}/paper-distribution'
    previous = c.get(url, headers=HEADERS).json()
    return c.put(url, headers=HEADERS, json={'subject_key': 'english', 'rows': rows,
        'expected_revision': previous['revision'],
        'expected_paper_version_id': previous['paper_version_id']})


def upload(c, exam_id, student, values):
    return c.put(f'/api/v1/exams/{exam_id}/item-scores', headers=HEADERS,
        json={'rows': [{'student_no': student['student_no'], 'question_no': number, 'score': value}
                       for number, value in values]})


def tracking(c, student):
    body = c.get(f"/api/v1/students/{student['id']}/profile", headers=HEADERS)
    assert body.status_code == 200, body.text
    return body.json()['question_type_tracking']


def test_defaults_persist_across_app_restart_and_new_exam_snapshot(client, tmp_path):
    result = write_default(client, [row('听力理解', '1～2'), row('阅读理解', '3')])
    assert result.status_code == 200, result.text
    assert [r['question_type'] for r in result.json()['rows']] == [
        '听力理解', '阅读理解', '完形填空', '词汇运用', '语法填空', '书面表达']
    exam, student = setup_exam(client)
    response = upload(client, exam['id'], student, [('1', 2), ('2', 1), ('3', 5)])
    assert response.status_code == 200, response.text
    with TestClient(create_app(Settings(data_dir=tmp_path))) as restarted:
        assert restarted.get(DEFAULT_URL, headers=HEADERS).json()['rows'][0]['question_numbers'] == '1～2'
        chart = tracking(restarted, student)
        assert chart['metric_key'] == 'mean_score'
        averages = {v['type']: v for v in chart['averages']}
        assert averages['听力理解']['mean_score'] == 1.5
        assert averages['阅读理解']['mean_score'] == 5
        assert averages['听力理解']['score_rate'] is None
        assert averages['语法填空']['mean_score'] is None
        with restarted.app.state.session_factory() as db:
            facts = list(db.scalars(select(StudentItemResult)))
            assert sorted(f.score for f in facts) == [1, 2, 5]
            assert all(f.correct is None and f.score_rate is None for f in facts)
            paper = db.scalar(select(ExamPaperVersion).where(ExamPaperVersion.status == 'confirmed'))
            assert paper.extraction_provider == 'teacher_distribution'
            details = get_student_score_details(db, exam_id=exam['id'], student_id=student['id'])
            assert details['section_scores'][0]['section_name'] == '听力理解'
            assert details['section_scores'][0]['full_score_known'] is False


def test_one_exam_adjustment_keeps_scores_and_default_independent(client):
    write_default(client, [row('听力理解', '1～2'), row('阅读理解', '3')])
    exam, student = setup_exam(client)
    assert upload(client, exam['id'], student, [('1', 2), ('2', 0), ('3', 3)]).status_code == 200
    response = configure(client, exam['id'], [row('听力理解', '1'), row('阅读理解', '2～3')])
    assert response.status_code == 200, response.text
    assert client.get(DEFAULT_URL, headers=HEADERS).json()['rows'][0]['question_numbers'] == '1～2'
    averages = {v['type']: v for v in tracking(client, student)['averages']}
    assert averages['听力理解']['mean_score'] == 2
    assert averages['阅读理解']['mean_score'] == 1.5  # genuine zero participates
    with client.app.state.session_factory() as db:
        results = list(db.scalars(select(StudentItemResult)))
        assert len(results) == 3
        assert sorted(r.score for r in results) == [0, 2, 3]
        assert {db.get(ExamQuestion, r.question_id).paper_version_id for r in results} == {response.json()['paper_version_id']}
        assert sorted(p.status for p in db.scalars(select(ExamPaperVersion))) == ['confirmed', 'superseded']
    # Changing defaults does not reinterpret this exam.
    assert write_default(client, [row('书面表达', '1～3')], revision=1).status_code == 200
    assert client.get(f"/api/v1/exams/{exam['id']}/paper-distribution", headers=HEADERS).json()['rows'][0]['question_numbers'] == '1'


def test_partial_section_never_creates_false_zero_or_biased_mean(client):
    write_default(client, [row('听力理解', '1～2'), row('阅读理解', '3')])
    exam, student = setup_exam(client)
    assert upload(client, exam['id'], student, [('1', 2), ('3', 0)]).status_code == 200
    chart = tracking(client, student)
    averages = {v['type']: v for v in chart['averages']}
    assert averages['听力理解']['mean_score'] is None
    assert averages['阅读理解']['mean_score'] == 0
    assert chart['incomplete_section_count'] == 1


@pytest.mark.parametrize('rows', [
    [row('听力理解', '1～3'), row('阅读理解', '3～4')],
    [row('听力理解', '3～1')], [row('听力理解', '0～3')],
    [row('听力理解', '0')], [row('听力理解', '1001')],
    [row('未知题型', '1')], [row('听力理解', '1'), row('听力理解', '2')],
])
def test_bad_defaults_are_atomic(client, rows):
    assert write_default(client, rows).status_code == 422
    assert client.get(DEFAULT_URL, headers=HEADERS).json()['revision'] == 0
    with client.app.state.session_factory() as db:
        assert db.get(AppSetting, 'paper_distribution:default:english') is None


def test_config_cannot_drop_a_scored_question_and_stale_editor_cannot_overwrite(client):
    write_default(client, [row('听力理解', '1～2')])
    exam, student = setup_exam(client)
    upload(client, exam['id'], student, [('1', 2), ('2', 1)])
    response = configure(client, exam['id'], [row('听力理解', '1')])
    assert response.status_code == 422 and '已有小分' in response.text
    assert len(tracking(client, student)['history'][0]['values']) == 1
    assert write_default(client, [row('听力理解', '1')], revision=0).status_code == 409
    wrong_scope = client.get(f"/api/v1/exams/{exam['id']}/paper-distribution?term_id=9999", headers=HEADERS)
    assert wrong_scope.status_code == 404
    wrong_subject = client.put(DEFAULT_URL, headers=HEADERS, json={
        'subject_key': 'math', 'rows': [], 'expected_revision': 1})
    assert wrong_subject.status_code == 409


def test_existing_pdf_question_metadata_and_known_marks_survive_reclassification(client):
    exam, student = setup_exam(client)
    with client.app.state.session_factory() as db:
        paper = ExamPaperVersion(exam_id=exam['id'], version=1, status='confirmed', source_attachment_ids_json=[])
        db.add(paper); db.flush()
        question = ExamQuestion(paper_version_id=paper.id, question_no='1', max_score=5,
            section_name='阅读理解', content_text='原阅读题', source_page=2,
            correct_answer_json={'answer': 'A'}, knowledge_nodes_json=['细节定位'])
        db.add(question); db.commit()
    assert upload(client, exam['id'], student, [('1', 4)]).status_code == 200
    response = configure(client, exam['id'], [row('任务型阅读', '1')])
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as db:
        question = db.scalar(select(ExamQuestion).where(ExamQuestion.paper_version_id == response.json()['paper_version_id']))
        assert question.content_text == '原阅读题' and question.source_page == 2
        assert question.max_score == 5 and question.correct_answer_json == {'answer': 'A'}
        assert question.knowledge_nodes_json == ['细节定位']
    chart = tracking(client, student)
    assert chart['metric_key'] == 'score_rate'
    assert next(v for v in chart['averages'] if v['type'] == '阅读理解')['score_rate'] == 80


@pytest.mark.parametrize("combined_number", [False, True])
def test_printed_subquestion_identity_is_preserved(client, combined_number):
    exam, student = setup_exam(client)
    with client.app.state.session_factory() as db:
        paper = ExamPaperVersion(exam_id=exam['id'], version=1, status='confirmed')
        db.add(paper); db.flush()
        db.add_all([ExamQuestion(paper_version_id=paper.id,
            question_no=f'1-{n}' if combined_number else '1',
            sub_question_no=None if combined_number else str(n),
            max_score=2, section_name='听力理解', question_type='选择题') for n in (1, 2)])
        db.commit()
    existing = client.get(f"/api/v1/exams/{exam['id']}/paper-distribution", headers=HEADERS).json()
    assert existing['rows'][0]['question_numbers'] == '1-1～1-1、1-2～1-2'
    result = configure(client, exam['id'], existing['rows'])
    assert result.status_code == 200, result.text
    assert upload(client, exam['id'], student, [('1-1', 2), ('1-2', 1)]).status_code == 200
    with client.app.state.session_factory() as db:
        questions = list(db.scalars(select(ExamQuestion).where(ExamQuestion.paper_version_id == result.json()['paper_version_id'])))
        assert [(q.question_no, q.sub_question_no) for q in questions] == (
            [('1-1', None), ('1-2', None)] if combined_number else [('1', '1'), ('1', '2')]
        )
    # Bare big-question numbers remain ambiguous rather than guessing a subquestion.
    assert upload(client, exam['id'], student, [('1', 1)]).status_code == 422


def test_chat_reads_the_same_configured_types_and_scores_without_invented_rates(client):
    from backend.app.services.student_learning import learning_evidence
    from backend.app.models import Exam
    write_default(client, [row('听力理解', '1～2')])
    exam, student = setup_exam(client)
    assert upload(client, exam['id'], student, [('1', 2), ('2', 1)]).status_code == 200
    with client.app.state.session_factory() as db:
        term_id = db.get(Exam, exam['id']).term_id
        evidence = learning_evidence(db, {'term_id': term_id, 'student_id': student['id']},
            SimpleNamespace(to_anonymous=lambda _: 'student_test'))
        section = evidence['recent_exams'][0]['sections'][0]
        assert section['section_name'] == '听力理解' and section['score'] == 3
        assert section['expected_items'] == section['scored_items'] == 2
        assert section['full_score_known'] is False
        assert not evidence['recent_exams'][0]['wrong_questions']
        assert '满分未知' in evidence['note']


def test_old_paper_facts_cannot_pollute_current_chart(client):
    write_default(client, [row('听力理解', '1')])
    exam, student = setup_exam(client)
    upload(client, exam['id'], student, [('1', 2)])
    with client.app.state.session_factory() as db:
        old_question_id = db.scalar(select(ExamQuestion.id))
    assert configure(client, exam['id'], [row('阅读理解', '1')]).status_code == 200
    with client.app.state.session_factory() as db:
        db.add(StudentItemResult(exam_id=exam['id'], student_id=student['id'], question_id=old_question_id, score=999))
        db.commit()
    values = {v['type']: v for v in tracking(client, student)['averages']}
    assert values['阅读理解']['mean_score'] == 2
    assert values['听力理解']['mean_score'] is None


def test_legacy_reading_migration_keeps_question_ids_scores_and_all_ranges(client):
    from alembic import command
    from sqlalchemy import text
    from backend.app.database import _alembic_config, run_migrations
    from backend.app.models import ScoreDimension, ExamDimensionScore, ExamScore, WorkspaceState, Exam
    exam, student = setup_exam(client)
    with client.app.state.session_factory() as db:
        paper = ExamPaperVersion(exam_id=exam['id'], version=1, status='confirmed')
        db.add(paper); db.flush()
        questions = [ExamQuestion(paper_version_id=paper.id, question_no='1',
                     section_name='阅读理解', question_type='阅读理解', max_score=5),
                     ExamQuestion(paper_version_id=paper.id, question_no='9',
                     section_name='阅读理解', question_type='阅读理解', max_score=15)]
        db.add_all(questions); db.flush()
        ids = [q.id for q in questions]
        paper_id = paper.id
        term_id = db.get(Exam, exam['id']).term_id
        db.add_all([StudentItemResult(exam_id=exam['id'], student_id=student['id'],
            question_id=q.id, score=score, source_record_id=f'original-{q.id}')
            for q, score in zip(questions, (4, 6))])
        dims = [ScoreDimension(exam_id=exam['id'], code='read', name='阅读理解', max_score=5),
                ScoreDimension(exam_id=exam['id'], code='task', name='任务型阅读', max_score=15)]
        db.add_all(dims); db.flush()
        score = db.scalar(select(ExamScore).where(ExamScore.exam_id == exam['id']))
        db.add_all([ExamDimensionScore(exam_score_id=score.id, dimension_id=d.id, score=s)
                    for d, s in zip(dims, (4, 6))])
        for key in ('paper_distribution:default:english', f"paper_distribution:exam:{exam['id']}"):
            db.add(AppSetting(key=key, value_json={'subject_key': 'english', 'revision': 2,
                'paper_version_id': paper_id, 'rows': [row('阅读理解', '1'), row('任务型阅读', '9')]}))
        db.add(WorkspaceState(term_id=term_id, revision=2, state_json={
            'errors': [{'id': 'legacy', 'type': '阅读理解', 'qnum': '9', 'reason': '保留原文'}]}))
        db.commit()
        # Simulate facts written by the previous version, bypassing new validators.
        db.execute(text("UPDATE exam_questions SET question_type='任务型阅读', section_name='任务型阅读' WHERE id=:id"), {'id': ids[1]})
        db.execute(text("UPDATE workspace_states SET state_json=:state WHERE term_id=:id"),
            {'state': '{"errors":[{"id":"legacy","type":"任务型阅读","qnum":"9","reason":"保留原文"}]}', 'id': term_id})
        db.commit()
        url = str(db.get_bind().url)
    config = _alembic_config(url)
    command.downgrade(config, '20260930_0034')
    run_migrations(url)
    for endpoint in (DEFAULT_URL, f"/api/v1/exams/{exam['id']}/paper-distribution"):
        value = client.get(endpoint, headers=HEADERS).json()
        assert value['revision'] == 3
        reading = next(r for r in value['rows'] if r['question_type'] == '阅读理解')
        assert reading['question_numbers'] == '1、9'
        assert all(r['question_type'] != '任务型阅读' for r in value['rows'])
    chart = tracking(client, student)
    assert '任务型阅读' not in chart['categories']
    assert next(r for r in chart['averages'] if r['type'] == '阅读理解')['score_rate'] == 50
    with client.app.state.session_factory() as db:
        facts = list(db.scalars(select(StudentItemResult)))
        assert [(r.question_id, r.score, r.source_record_id) for r in facts] == [
            (ids[0], 4, f'original-{ids[0]}'), (ids[1], 6, f'original-{ids[1]}')]
        assert all(db.get(ExamQuestion, i).question_type == '阅读理解' for i in ids)
        dims = list(db.scalars(select(ScoreDimension)))
        assert [(d.name, d.max_score) for d in dims] == [('阅读理解', 20)]
        assert db.scalar(select(ExamDimensionScore.score)) == 10
        state = db.get(WorkspaceState, term_id)
        assert state.revision == 3
        assert state.state_json['errors'][0]['type'] == '阅读理解'
        assert state.state_json['errors'][0]['reason'] == '保留原文'
    # Both old clients and new settings can re-import/save without losing scores.
    response = client.put(f"/api/v1/exams/{exam['id']}/item-scores", headers=HEADERS,
        json={'rows': [{'student_no': student['student_no'], 'question_type': '任务型阅读',
                        'question_no': '9', 'score': 6}]})
    assert response.status_code == 200, response.text
    assert configure(client, exam['id'], [row('阅读理解', '1、9')]).status_code == 200


def test_legacy_default_payload_merges_reading_without_filling_gaps(client):
    response = write_default(client, [row('阅读理解', '1～3'), row('任务型阅读', '8～9')])
    assert response.status_code == 200, response.text
    assert next(r for r in response.json()['rows'] if r['question_type'] == '阅读理解')['question_numbers'] == '1～3、8～9'
    exam, student = setup_exam(client)
    assert upload(client, exam['id'], student, [('1', 1), ('2', 2), ('3', 3), ('8', 4), ('9', 5)]).status_code == 200
    values = {r['type']: r for r in tracking(client, student)['averages']}
    assert values['阅读理解']['mean_score'] == 3
    with client.app.state.session_factory() as db:
        assert {q.question_no for q in db.scalars(select(ExamQuestion))} == {'1', '2', '3', '8', '9'}
        assert {q.question_type for q in db.scalars(select(ExamQuestion))} == {'阅读理解'}


def test_legacy_exam_dimensions_are_merged_on_new_exam_import(client):
    response = client.post('/api/v1/exams', headers=HEADERS, json={'name': '旧格式题型分',
        'exam_type': 'question_type', 'dimensions': [
            {'code': 'reading', 'name': '阅读理解', 'max_score': 30},
            {'code': 'task', 'name': '任务型阅读', 'max_score': 10}]})
    assert response.status_code == 201, response.text
    assert [(d['name'], d['max_score']) for d in response.json()['dimensions']] == [('阅读理解', 40)]
