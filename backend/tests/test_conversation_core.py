"""Synthetic multi-turn replay, no model/network calls or real student records."""
import pytest
from sqlalchemy import select
from fastapi import HTTPException
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import Term, Class, Student, Enrollment, AnalysisRun, AgentSession
from backend.app.services.agent_analysis.conversation import prepare_turn, resolve_student, mapper_for_run, run_mapper
from backend.app.agent.privacy import PrivacyMapper


@pytest.fixture
def world(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with app.state.session_factory() as db:
        term = db.scalar(select(Term).where(Term.status == "active"))
        classes = [Class(term_id=term.id, name=n) for n in ["一班", "二班"]]
        students = [Student(name=n, student_no=f"TEST{i:03}") for i, n in enumerate(["张三", "李四", "张三", "王五"])]
        db.add_all(classes + students); db.flush()
        for i, s in enumerate(students):
            db.add(Enrollment(term_id=term.id, class_id=classes[0 if i < 2 else 1].id, student_id=s.id, status="active"))
        session = AgentSession(term_id=term.id, subject_key="english", title="合成回放")
        db.add(session); db.commit()
        yield db, session, {"term_id": term.id}, students, classes


def turn(world, content, selected=None, aliases=None):
    db, session, scope, _, _ = world
    state, snapshot, context, focus = prepare_turn(db, session.id, scope, content, selected, aliases)
    run = AnalysisRun(session_id=session.id, term_id=scope["term_id"], capability="general_chat", status="completed",
        input_summary_json={"conversation_state": state, "identity_snapshot": snapshot})
    db.add(run); db.commit()
    return run, context, focus


def test_duplicate_choice_number_switch_comparison_and_class(world):
    db, _, scope, s, classes = world
    run, context, focus = turn(world, "看看张三")
    assert not focus and context["resolution_status"] == "ambiguous"
    assert len(context["candidates"]) == 2
    ref = context["candidates"][0]["student_ref"]
    assert turn(world, "就是这位", [ref])[2] == [s[0].id]
    assert turn(world, "这部分还没教，先不认定错因")[2] == [s[0].id]
    assert turn(world, "李四呢")[2] == [s[1].id]
    assert set(turn(world, "和一班张三比较")[2]) == {s[0].id, s[1].id}
    assert turn(world, "全班呢")[2] == []
    assert turn(world, "看看全班成绩")[1]['resolution_status'] == 'unspecified'
    assert turn(world, "TEST002这次如何")[2] == [s[2].id]
    assert "还没教" in str(turn(world, "继续")[1])
    mapper = mapper_for_run(db, {**scope, "run_id": run.id})
    assert mapper.sanitize_text("TEST000") == ref


def test_stable_refs_and_frozen_historical_names(world):
    db, _, scope, s, _ = world
    run, _, _ = turn(world, "李四呢")
    before = mapper_for_run(db, {**scope, "run_id": run.id})
    ref = before.to_anonymous(s[1].id)
    s[1].name = "李小四"; db.commit()
    new, _, focus = turn(world, "李小四")
    assert focus == [s[1].id]
    assert mapper_for_run(db, {**scope, "run_id": new.id}).to_anonymous(s[1].id) == ref
    assert mapper_for_run(db, {**scope, "run_id": run.id}).restore_text_for_display(ref) == "李四"
    strict = PrivacyMapper.from_snapshot(before.snapshot(), allow_student_names=False)
    assert "李四" not in strict.sanitize_text("李四的结果")
    assert strict.restore_text_for_display(strict.to_anonymous(s[1].id)) == "李四"


def test_alias_explicit_confirmation_and_fuzzy_never_automatches(world):
    db, _, scope, s, _ = world
    mapper = run_mapper(db, scope)
    ref = mapper.to_anonymous(s[1].id)
    assert turn(world, "小李", aliases={"小李": ref})[2] == [s[1].id]
    assert turn(world, "全班")[2] == []
    assert turn(world, "小李情况")[2] == [s[1].id]
    result = resolve_student(db, scope, "李似", mapper)
    assert result["status"] == "not_found" and not result["matches"]
    with pytest.raises(HTTPException):
        turn(world, "继续", selected=["student_9999"])
    with pytest.raises(HTTPException):
        turn(world, "继续", aliases={"张三": ref})


def test_read_cache_invalidates_names_and_limits_repeated_calls(world):
    from backend.app.agent.education_bridge.bridge_cli import run_tool
    db, _, scope, s, _ = world
    run, _, _ = turn(world, "李四")
    scope = {**scope, "run_id": run.id}
    first = run_tool('resolve_student', db, scope, {"query":"李四"})
    second = run_tool('resolve_student', db, scope, {"query":"李四"})
    assert first['data'] == second['data']
    db.refresh(run); assert run.input_summary_json['query_stats']['cache_hits'] == 1
    s[1].name = '李小四'; db.commit()
    third = run_tool('resolve_student', db, scope, {"query":"李四"})
    assert third['data']['status'] == 'not_found'
    db.refresh(run); assert run.input_summary_json['query_stats']['cache_hits'] == 1
    for _ in range(17): run_tool('resolve_student', db, scope, {"query":"李小四"})
    with pytest.raises(HTTPException) as exc:
        run_tool('resolve_student', db, scope, {"query":"李小四"})
    assert exc.value.status_code == 429


def test_strict_mode_persists_to_followup_and_scoped_roster_never_guesses(world):
    db, session, scope, s, classes = world
    state, snap, _, _ = prepare_turn(db, session.id, scope, "李四", identity_mode='strict')
    run = AnalysisRun(session_id=session.id, term_id=scope['term_id'], capability='general_chat', status='completed',
        input_summary_json={'identity_snapshot':snap,'conversation_state':state})
    db.add(run); db.commit()
    _, nextsnap, _, _ = prepare_turn(db, session.id, scope, '他呢')
    assert nextsnap['allow_student_names'] is False
    restricted = {**scope, 'class_id':classes[0].id}
    mapper = run_mapper(db, restricted)
    assert resolve_student(db, restricted, '王五', mapper)['status'] == 'not_found'


def test_unknown_named_person_does_not_inherit_previous_person(world):
    assert turn(world, '李四呢')[2]
    _, context, focus = turn(world, '看看赵六的成绩')
    assert not focus and context['resolution_status'] == 'not_found'
    assert context['unresolved_queries'] == ['赵六']
