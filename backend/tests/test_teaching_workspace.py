"""Teaching workflow persistence, scope integrity and honest assessment semantics."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import AgentMessage, AgentSession, AnalysisRun, Class, Enrollment, Exam, Student, Term
from backend.app.models.teaching_entities import TeachingTask
from backend.app.services.agent_analysis.sessions import SessionService
from backend.app.services.teaching import build_task_context

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = "/api/v1/teaching/tasks"


@pytest.fixture
def setup(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with app.state.session_factory() as db:
        term = db.scalar(select(Term).where(Term.status == "active"))
        classroom = Class(term_id=term.id, name="讲评一班")
        other = Class(term_id=term.id, name="另一班")
        students = [Student(student_no="T01", name="甲"), Student(student_no="T02", name="乙")]
        db.add_all([classroom, other, *students]); db.flush()
        for index, student in enumerate(students):
            db.add(Enrollment(term_id=term.id, class_id=(classroom if index == 0 else other).id,
                              student_id=student.id, status="active"))
        exam = Exam(term_id=term.id, name="讲评考试", full_score=100)
        db.add(exam); db.commit()
        scope = {"term_id": term.id, "class_id": classroom.id, "exam_id": exam.id}
        ids = [s.id for s in students]
    with TestClient(app) as client:
        task = client.post(BASE, headers=HEADERS, json={**scope, "title": "推断题讲评",
            "goal": "能用原文证据解释推断", "constraints": {"lesson_minutes": 35, "no_homework": True}})
        assert task.status_code == 201, task.text
        yield client, task.json(), ids


def test_task_survives_new_sessions_and_optimistic_conflict(setup):
    client, task, _ = setup
    sessions = [client.post(f"{BASE}/{task['id']}/sessions", headers=HEADERS, json={}).json() for _ in range(2)]
    assert sessions[0]["id"] != sessions[1]["id"]
    assert all(s["teaching_task_id"] == task["id"] for s in sessions)
    patch = {"expected_revision": 1, "goal": "独立定位证据", "phase": "design"}
    saved = client.patch(f"{BASE}/{task['id']}", headers=HEADERS, json=patch)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert client.patch(f"{BASE}/{task['id']}", headers=HEADERS, json=patch).status_code == 409
    assert len(client.get(f"{BASE}/{task['id']}", headers=HEADERS).json()["session_ids"]) == 2


def test_material_revisions_restore_without_erasing_history(setup):
    client, task, _ = setup
    path = f"{BASE}/{task['id']}/artifacts"
    content = {"kind": "student_handout", "title": "学生练习", "body": "原始问题", "items": ["定位原文"]}
    artifact = client.post(path, headers=HEADERS, json=content).json()
    saved = client.put(f"{path}/{artifact['id']}", headers=HEADERS,
                       json={**content, "body": "教师修改", "expected_revision": 1})
    assert saved.json()["revision"] == 2
    assert client.put(f"{path}/{artifact['id']}", headers=HEADERS,
                      json={**content, "expected_revision": 1}).status_code == 409
    restored = client.post(f"{path}/{artifact['id']}/restore", headers=HEADERS,
                          json={"revision": 1, "expected_revision": 2})
    assert restored.json()["revision"] == 3
    assert restored.json()["body"] == "原始问题"
    revisions = client.get(f"{path}/{artifact['id']}/revisions", headers=HEADERS).json()
    assert len(revisions) == 3
    assert revisions[1]["content"]["body"] == "教师修改"


def test_assessment_checks_scope_atomically_and_does_not_award_mastery(setup):
    client, task, students = setup
    path = f"{BASE}/{task['id']}"
    row = {"student_id": students[0], "objective": "推断", "correct": True,
           "independent": False, "new_question": True, "observed_on": "2026-09-29"}
    body = {"kind": "assessment", "note": "使用提示后答对", "observations": [row]}
    assert client.post(path + "/feedback", headers=HEADERS,
        json={**body, "observations": [row, {**row, "student_id": students[1]}]}).status_code == 400
    assert client.get(path, headers=HEADERS).json()["feedback"] == []
    assert client.post(path + "/feedback", headers=HEADERS, json=body).status_code == 201
    review = client.get(path, headers=HEADERS).json()["review"]
    assert review["mastery_status"] == "pending_evidence"
    assert review["objectives"][0]["correct"] == 1
    assert review["objectives"][0]["independent_new_correct"] == 0
    # Correcting a same-day observation replaces the count, not the original record.
    body["observations"] = [{**row, "correct": False, "independent": True}]
    assert client.post(path + "/feedback", headers=HEADERS, json=body).status_code == 201
    detail = client.get(path, headers=HEADERS).json()
    assert len(detail["feedback"]) == 2
    assert detail["review"]["objectives"][0]["attempts"] == 1
    assert detail["review"]["objectives"][0]["correct"] == 0


def test_completed_and_archived_tasks_are_recoverable_but_block_writes(setup):
    client, task, _ = setup
    path = f"{BASE}/{task['id']}"
    assert client.patch(path, headers=HEADERS, json={"expected_revision": 1, "phase": "completed"}).status_code == 200
    assert client.post(path + "/feedback", headers=HEADERS,
                       json={"kind": "observation", "note": "不能静默修改"}).status_code == 409
    assert client.patch(path, headers=HEADERS, json={"expected_revision": 2, "goal": "静默修改"}).status_code == 409
    assert client.patch(path, headers=HEADERS, json={"expected_revision": 2, "phase": "archived"}).status_code == 200
    assert client.get(BASE, headers=HEADERS, params={"term_id": task["term_id"]}).json() == []
    assert len(client.get(BASE, headers=HEADERS,
                          params={"term_id": task["term_id"], "include_archived": True}).json()) == 1
    assert client.patch(path, headers=HEADERS, json={"expected_revision": 3, "phase": "review"}).status_code == 200


def test_context_carries_goal_conditions_and_teacher_correction_without_raw_student_ids(setup):
    client, task, _ = setup
    path = f"{BASE}/{task['id']}"
    conversation = client.post(path + "/sessions", headers=HEADERS, json={}).json()
    client.post(path + "/feedback", headers=HEADERS, json={"kind": "correction",
        "note": "这部分尚未教过，先做示范", "correction_reason": "not_taught", "finding_title": "推断失误"})
    for _ in range(5):
        client.post(path + "/feedback", headers=HEADERS, json={"kind": "implementation", "note": "后续课堂活动"})
    with client.app.state.session_factory() as db:
        context = build_task_context(db, task["id"])
        assert "35" in context and "尚未教过" in context and "no_homework" in context
        assert "student_id" not in context
        run = AnalysisRun(session_id=conversation["id"], term_id=task["term_id"], capability="general_chat",
                          subject_key="english", input_summary_json={"teaching_context": context})
        db.add(run); db.commit()
        stored_task = db.get(TeachingTask, task["id"])
        stored_task.goal = "随后修改的目标"; db.commit()
        messages = SessionService(db).build_multi_turn_messages(session_id=conversation["id"],
            system_prompt="教师助手", user_message="继续", current_run_id=run.id, include_formal_context=False)
        text_content = "\n".join(m["content"] for m in messages)
        assert task["goal"] in text_content
        assert "随后修改的目标" not in text_content


def test_report_adoption_is_scoped_idempotent_and_keeps_teacher_edits(setup):
    client, task, _ = setup
    path = f"{BASE}/{task['id']}"
    conversation = client.post(path + "/sessions", headers=HEADERS, json={}).json()
    with client.app.state.session_factory() as db:
        run = AnalysisRun(session_id=conversation["id"], term_id=task["term_id"], class_id=task["class_id"],
                          exam_id=task["exam_id"], subject_key="english", capability="review_plan", status="completed")
        db.add(run); db.flush()
        db.add(AgentMessage(session_id=conversation["id"], analysis_run_id=run.id, role="assistant",
            structured_answer_json={"summary": "讲评", "sections": [{"kind": "student_handout", "title": "练习",
                                    "body": "AI 原稿", "items": []}]},
            teacher_material_edits_json={"0": {"body": "教师版本", "items": ["检查证据"]}}))
        db.commit(); run_id = run.id
    first = client.post(path + f"/adopt/{run_id}", headers=HEADERS, json={})
    assert first.status_code == 200, first.text
    assert first.json()["artifacts"][0]["body"] == "教师版本"
    second = client.post(path + f"/adopt/{run_id}", headers=HEADERS, json={})
    assert second.json()["artifacts"][0]["id"] == first.json()["artifacts"][0]["id"]
    with client.app.state.session_factory() as db:
        db.get(AnalysisRun, run_id).class_id = None; db.commit()
    assert client.post(path + f"/adopt/{run_id}", headers=HEADERS, json={}).status_code == 409


def test_task_scope_subject_and_authentication_boundaries(setup):
    client, task, _ = setup
    assert client.get(BASE, params={"term_id": task["term_id"]}).status_code == 401
    assert client.post(BASE, headers=HEADERS, json={"term_id": 99999, "title": "跨学期", "goal": "检查"}).status_code == 400
    assert client.patch("/api/v1/settings", headers=HEADERS, json={"subject_key": "math"}).status_code == 409
    assert client.patch(f"{BASE}/{task['id']}", headers=HEADERS,
                        json={"expected_revision": 1, "goal": None}).status_code == 422


def test_additive_migration_preserves_existing_conversation_and_report(tmp_path):
    from alembic import command
    from backend.app.database import _alembic_config, create_session_factory, run_migrations
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260927_0032")
    factory = create_session_factory(database_url)
    with factory() as db:
        term_id = db.execute(text("INSERT INTO terms (code, name, starts_on, ends_on, status, created_at, updated_at) VALUES ('test-migration', '测试', '2026-09-01', '2027-01-31', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id")).scalar_one()
        session_id = db.execute(text("INSERT INTO agent_sessions (title,term_id,subject_key,status,created_at,updated_at) VALUES ('旧版讲评', :term,'english','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) RETURNING id"), {"term": term_id}).scalar_one()
        old_run = AnalysisRun(session_id=session_id, term_id=term_id, capability="exam_analysis", status="completed")
        db.add(old_run); db.flush()
        old_message = AgentMessage(session_id=session_id, analysis_run_id=old_run.id, role="assistant",
                                   structured_answer_json={"summary": "旧报告内容"})
        db.add(old_message); db.commit(); message_id = old_message.id
    command.upgrade(config, "20260930_0033")
    with factory() as db:
        old_task_id = db.execute(text("INSERT INTO teaching_tasks (term_id,subject_key,title,goal,phase,constraints_json,revision,created_at,updated_at) VALUES (:term,'english','旧任务','保留目标','design','{}',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) RETURNING id"), {"term": term_id}).scalar_one()
        db.execute(text("INSERT INTO teaching_artifacts (task_id,kind,title,body,items_json,revision,created_at,updated_at) VALUES (:task,'student_handout','旧练习','保留题干','[]',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {"task": old_task_id})
        db.commit()
    run_migrations(database_url)
    with factory() as db:
        conversation = db.get(AgentSession, session_id)
        assert conversation.title == "旧版讲评"
        old_task = db.get(TeachingTask, old_task_id)
        assert old_task.goal == "保留目标" and old_task.target_type == "class" and old_task.student_ids_json == []
        assert db.execute(text("SELECT body FROM teaching_artifacts WHERE task_id = :task"), {"task":old_task_id}).scalar_one() == "保留题干"
        assert conversation.teaching_task_id is None
        assert db.get(AgentMessage, message_id).structured_answer_json["summary"] == "旧报告内容"
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261001_0035"


def test_task_context_budget_keeps_valid_json_and_focuses_requested_material(setup):
    import json
    from backend.app.agent.token_budget import estimate_text_tokens
    client, task, students = setup
    path = f"{BASE}/{task['id']}"
    updated = client.patch(path, headers=HEADERS, json={"expected_revision": 1,
        "goal": "教学目标" * 500,
        "constraints": {"lesson_minutes": 35, "taught_content": "已教内容" * 375,
                        "curriculum": "教材" * 150, "activity_preference": "活动" * 250,
                        "equipment": "设备" * 150, "grade": "年级" * 50}})
    assert updated.status_code == 200
    artifact_ids = []
    for i in range(5):
        created = client.post(path + "/artifacts", headers=HEADERS, json={"kind": "student_handout",
            "title": str(i), "body": ("待修改材料" if i == 0 else "其他材料") * 2000,
            "items": ["步骤" * 1000] * 20})
        assert created.status_code == 201
        artifact_ids.append(created.json()["id"])
        client.post(path + "/feedback", headers=HEADERS, json={"kind": "correction", "note": "修正内容" * 750})
    with client.app.state.session_factory() as db:
        for focus in (None, artifact_ids[0]):
            context = build_task_context(db, task["id"], focus)
            assert estimate_text_tokens(context) <= 3500
            data = json.loads(context.split("\n")[1])
            assert data["constraints"]["lesson_minutes"] == 35
            assert data["goal"].startswith("教学目标")
            assert data["teacher_feedback"][-1]["note"].startswith("修正内容")
            if focus:
                assert data["focus_artifact_id"] == focus
                assert [a["id"] for a in data["materials"]] == [focus]
                assert data["materials"][0]["excerpt"].startswith("待修改材料")
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            build_task_context(db, task["id"], 999999)
    session = client.post(path + "/sessions", headers=HEADERS, json={}).json()
    bad = client.post(f"/api/v1/agent/sessions/{session['id']}/messages", headers=HEADERS,
                      json={"content": "修改", "teaching_artifact_id": 999999})
    assert bad.status_code == 404


def test_create_from_report_is_atomic_and_inherits_original_scope(setup):
    client, task, students = setup
    with client.app.state.session_factory() as db:
        run = AnalysisRun(term_id=task["term_id"], class_id=task["class_id"], exam_id=task["exam_id"],
                          subject_key="english", capability="exam_analysis", status="failed")
        db.add(run); db.commit(); run_id = run.id
    data = {"title": "报告后的讲评", "goal": "检验推断依据"}
    endpoint = BASE + f"/from-report/{run_id}"
    assert client.post(endpoint, headers=HEADERS, json=data).status_code == 409
    assert len(client.get(BASE, headers=HEADERS, params={"term_id": task["term_id"]}).json()) == 1
    with client.app.state.session_factory() as db:
        run = db.get(AnalysisRun, run_id); run.status = "completed"
        session = AgentSession(term_id=task["term_id"], title="原报告", subject_key="english")
        db.add(session); db.flush()
        run.session_id = session.id
        db.add(AgentMessage(session_id=session.id, analysis_run_id=run.id, role="assistant",
            structured_answer_json={"summary": "需核对推断过程", "findings": [], "recommendations": []}))
        db.commit()
    created = client.post(endpoint, headers=HEADERS, json=data)
    assert created.status_code == 201, created.text
    assert created.json()["class_id"] == task["class_id"]
    assert created.json()["exam_id"] == task["exam_id"]
    assert created.json()["artifacts"][0]["source_run_id"] == run_id
    with client.app.state.session_factory() as db:
        db.get(AnalysisRun, run_id).student_id = students[0]; db.commit()
    personal = client.post(endpoint, headers=HEADERS, json=data)
    assert personal.status_code == 201, personal.text
    assert personal.json()["target_type"] == "individual"
    assert personal.json()["student_ids"] == [students[0]]
