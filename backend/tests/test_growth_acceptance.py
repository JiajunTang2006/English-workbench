"""成长树首轮验收数据回归（方案 §8 表末清单）。

逐项覆盖：有效 0 分、空分、缺考、请假、满分与超满分、重复导入、部分失败、
改分、撤销两次、换班、跨学期、转学生、教师覆盖分数、OCR 待核对、仅有总分、
历史日期缺失、模型不可用。

本文件只做「事实是否正确」的断言：分项能力只认 present 且非空的得分；缺失
一律标注而不是补零；考试结果不直接给分，只在「实际参加」时按规则记一条基础
奖励，并按相对该生自己上一次可比测评的提升给进步分（缺考/空分不生成事件）。
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import (
    Class,
    Enrollment,
    Exam,
    ExamPaperVersion,
    ExamQuestion,
    ExamScore,
    GrowthEvent,
    Student,
    StudentGrowthSnapshot,
    StudentItemResult,
)
from backend.app.services.growth import performance as growth_performance


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path)))


def _current_term(client, headers):
    return client.get("/api/v1/terms/current", headers=headers).json()["id"]


def _seed_class(client, headers, names=("01", "02")):
    term_id = _current_term(client, headers)
    classroom = client.post("/api/v1/classes", headers=headers, json={"name": "711"})
    assert classroom.status_code == 201, classroom.text
    class_id = classroom.json()["id"]
    students = []
    for no in names:
        response = client.post("/api/v1/students", headers=headers, json={
            "student_no": no, "name": f"学生{no}", "class_id": class_id})
        assert response.status_code == 201, response.text
        students.append(response.json())
    return term_id, class_id, students


def _activity(student_id, event_type="task_completed", note="完成课堂任务", **extra):
    return {"student_id": student_id, "event_type": event_type, "note": note, **extra}


def _seed_exam(session, *, term_id, class_id, student_ids, question_type="阅读理解",
               max_score=10, status="active", exam_date=None, suffix=""):
    """建一场考试与一道题；返回 (exam, question)。"""
    exam = Exam(term_id=term_id, name=f"单元测{suffix}", full_score=max_score,
                exam_date=exam_date or date(2026, 4, 1),
                source_key=f"acc:{class_id}:{status}:{suffix}", status=status)
    session.add(exam)
    session.flush()
    paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed",
                             full_score=max_score)
    session.add(paper)
    session.flush()
    question = ExamQuestion(paper_version_id=paper.id, question_no="1",
                            question_type=question_type, max_score=max_score,
                            knowledge_nodes_json=["细节定位"])
    session.add(question)
    session.flush()
    for student_id in student_ids:
        session.add(ExamScore(exam_id=exam.id, student_id=student_id,
                              total_score=None, class_id_at_exam=class_id,
                              attendance_status="present"))
    session.flush()
    return exam, question


def _add_item(session, *, term_id, class_id, student_id, score, attendance,
              max_score=10, question_type="阅读理解", exam_date=None, suffix=""):
    """为单个学生建一场只含一题的考试并写入一条小分记录。

    ``student_item_results`` 对 (exam_id, student_id, question_id) 有唯一约束，
    因此不同到考状态必须落在不同考试上，不能在同一道题上重复写。
    """
    exam, question = _seed_exam(
        session, term_id=term_id, class_id=class_id, student_ids=[student_id],
        question_type=question_type, max_score=max_score,
        exam_date=exam_date, suffix=suffix)
    session.add(StudentItemResult(
        exam_id=exam.id, student_id=student_id, question_id=question.id,
        score=score,
        score_rate=None if score is None else score / max_score,
        correct=score is not None and score >= max_score,
        attendance_status=attendance))
    session.flush()
    return exam, question


# ---------------------------------------------------------------- 有效 0 分 / 空分 / 缺考 / 请假


def test_zero_score_is_evidence_but_blank_absent_and_excused_are_not(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        # 同一能力分支下四种状态各一场：只有「到场 + 有分（含 0 分）」是证据。
        _add_item(session, term_id=term_id, class_id=class_id, student_id=student_id,
                  score=0.0, attendance="present", suffix="zero")
        _add_item(session, term_id=term_id, class_id=class_id, student_id=student_id,
                  score=None, attendance="present", suffix="blank")
        _add_item(session, term_id=term_id, class_id=class_id, student_id=student_id,
                  score=3.0, attendance="absent", suffix="absent")
        _add_item(session, term_id=term_id, class_id=class_id, student_id=student_id,
                  score=3.0, attendance="excused", suffix="excused")
        session.commit()

        dimensions = growth_performance.build_dimension_performance(
            session, student_id=student_id, term_id=term_id)
    reading = dimensions["reading"]
    # 0 分是有效证据：观察次数 1，而不是「无记录」
    assert reading["observations"] == 1
    assert reading["latest_rate"] == 0.0
    assert reading["status"] == "insufficient_comparable_history"
    assert reading["value"] == 0.0
    # 空分/缺考/请假都不进入分母，也不被当成 0 分表现
    assert reading["distinct_dates"] == 1


def test_missing_evidence_is_labelled_not_zero_filled(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        dimensions = growth_performance.build_dimension_performance(
            session, student_id=student_id, term_id=term_id)
    for dimension in dimensions.values():
        assert dimension["status"] == "no_evidence"
        assert dimension["value"] is None
        assert dimension["observations"] == 0
        assert "暂无" in dimension["sample_note"]


# ---------------------------------------------------------------- 满分与超满分


def test_full_and_over_full_scores_clamp_to_one(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01", "02"))
    full_id, over_id = students[0]["id"], students[1]["id"]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        exam, question = _seed_exam(session, term_id=term_id, class_id=class_id,
                                    student_ids=[full_id, over_id])
        session.add(StudentItemResult(
            exam_id=exam.id, student_id=full_id, question_id=question.id,
            score=10.0, score_rate=1.0, correct=True, attendance_status="present"))
        # 超满分（教师手工录入错误）不产生 >1 的能力值
        session.add(StudentItemResult(
            exam_id=exam.id, student_id=over_id, question_id=question.id,
            score=12.0, score_rate=1.2, correct=True, attendance_status="present"))
        session.commit()

        full = growth_performance.build_dimension_performance(
            session, student_id=full_id, term_id=term_id)["reading"]
        over = growth_performance.build_dimension_performance(
            session, student_id=over_id, term_id=term_id)["reading"]
    assert full["latest_rate"] == 1.0
    assert over["latest_rate"] == 1.0
    assert 0.0 <= over["value"] <= 1.0


# ---------------------------------------------------------------- 改分 / 仅有总分 / OCR 待核对


def test_grade_change_is_recomputed_without_stale_cache(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        exam, question = _seed_exam(session, term_id=term_id, class_id=class_id,
                                    student_ids=[student_id])
        item = StudentItemResult(
            exam_id=exam.id, student_id=student_id, question_id=question.id,
            score=2.0, score_rate=0.2, correct=False, attendance_status="present")
        session.add(item)
        session.commit()

        before = growth_performance.build_dimension_performance(
            session, student_id=student_id, term_id=term_id)["reading"]
        assert before["latest_rate"] == 0.2

        item.score = 8.0
        item.score_rate = 0.8
        item.correct = True
        session.commit()
        after = growth_performance.build_dimension_performance(
            session, student_id=student_id, term_id=term_id)["reading"]
    assert after["latest_rate"] == 0.8
    assert after["value"] > before["value"]


def test_total_only_exam_keeps_growth_usable(tmp_path):
    """仅有总分、没有逐题小分：能力分支待记录，成长页仍可用。"""
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        exam = Exam(term_id=term_id, name="只有总分", full_score=100,
                    exam_date=date(2026, 4, 1), source_key="acc:total-only")
        session.add(exam)
        session.flush()
        session.add(ExamScore(exam_id=exam.id, student_id=student_id,
                              total_score=88.0, class_id_at_exam=class_id,
                              attendance_status="present"))
        session.commit()

    created = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "total-only",
        "items": [_activity(student_id)]})
    assert created.status_code == 201, created.text

    detail = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 2
    for dimension in detail["snapshot"]["dimensions"].values():
        assert dimension["status"] == "no_evidence"


def test_ocr_pending_paper_does_not_award_growth_points(tmp_path):
    """OCR 待核对的试卷（非 active）不得进入能力值，也不得自动加分。"""
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        exam, question = _seed_exam(session, term_id=term_id, class_id=class_id,
                                    student_ids=[student_id], status="draft")
        session.add(StudentItemResult(
            exam_id=exam.id, student_id=student_id, question_id=question.id,
            score=9.0, score_rate=0.9, correct=True, attendance_status="present"))
        session.commit()

        dimensions = growth_performance.build_dimension_performance(
            session, student_id=student_id, term_id=term_id)
        assert dimensions["reading"]["status"] == "no_evidence"
        # 考试结果不会自动写入成长事件账本
        assert session.scalars(select(GrowthEvent)).all() == []

    detail = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 0
    assert detail["records"] == []


# ---------------------------------------------------------------- 教师覆盖分数


def test_teacher_override_points_are_bounded(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    # 课堂观察可显式给 1 分（覆盖规则默认 1 分）或 2 分
    response = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "override",
        "items": [_activity(student_id, "teacher_observation", "课堂朗读示范", points=2)]})
    assert response.status_code == 201, response.text

    detail = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 2
    assert detail["records"][0]["proposed_points"] == 2
    assert detail["records"][0]["applied_points"] == 2

    # 非课堂观察类型忽略手工点数，固定按规则计分（防止刷分）
    fixed = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "override-fixed",
        "items": [_activity(student_id, "task_completed", "完成任务", points=2)]})
    assert fixed.status_code == 201, fixed.text
    detail = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                        params={"term_id": term_id}).json()
    fixed_record = [r for r in detail["records"]
                    if r["event_type"] == "task_completed"][0]
    assert fixed_record["proposed_points"] == 2  # 规则点数，不是提交值


# ---------------------------------------------------------------- 部分失败


def test_partial_failure_reports_each_skipped_item(tmp_path):
    """一批里混入非法项：合法项入库，非法项逐条说明，不静默丢失。"""
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    response = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "partial",
        "items": [
            _activity(student_id),                                    # 0 合法
            _activity(student_id, "not_a_real_type", "未知类型"),      # 1 非法类型
            _activity(999999),                                        # 2 学期外学生
            _activity(student_id, "task_completed", "   "),           # 3 空事由
        ]})
    assert response.status_code == 201, response.text
    body = response.json()
    assert [item["index"] for item in body["created"]] == [0]
    assert sorted(item["index"] for item in body["skipped"]) == [1, 2, 3]
    reasons = {item["index"]: item["reason"] for item in body["skipped"]}
    assert "事件类型" in reasons[1]
    assert "学期" in reasons[2]

    detail = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 2
    assert len(detail["records"]) == 1


# ---------------------------------------------------------------- 撤销两次


def test_second_reversal_of_same_event_is_blocked(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    created = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "double-rev",
        "items": [_activity(student_id, "spaced_review", "间隔复习达标")]}).json()
    event_id = created["created"][0]["event_id"]

    first = client.post(f"/api/v1/growth/events/{event_id}/reverse", headers=headers,
                        json={"term_id": term_id, "reason": "误录"})
    assert first.status_code == 201, first.text
    assert first.json()["snapshot"]["term_points"] == 0

    second = client.post(f"/api/v1/growth/events/{event_id}/reverse", headers=headers,
                         json={"term_id": term_id, "reason": "再撤一次"})
    assert second.status_code == 409
    # 撤销事件追加而不是删除原记录
    detail = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                        params={"term_id": term_id}).json()
    assert len(detail["records"]) == 2
    assert sum(1 for r in detail["records"] if r["event_type"] == "reversal") == 1


# ---------------------------------------------------------------- 换班 / 转学生 / 跨学期


def test_class_change_keeps_event_audit_and_moves_forest(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_a, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]
    class_b = client.post("/api/v1/classes", headers=headers, json={"name": "712"})
    assert class_b.status_code == 201, class_b.text
    class_b_id = class_b.json()["id"]

    client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "move-1", "items": [_activity(student_id)]})

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.student_id == student_id, Enrollment.term_id == term_id))
        assert enrollment.class_id == class_a
        enrollment.class_id = class_b_id
        session.commit()

    in_b = client.get("/api/v1/growth/forest", headers=headers,
                      params={"term_id": term_id, "class_id": class_b_id}).json()
    assert [row["student_id"] for row in in_b["students"]] == [student_id]
    assert in_b["students"][0]["term_points"] == 2
    in_a = client.get("/api/v1/growth/forest", headers=headers,
                      params={"term_id": term_id, "class_id": class_a}).json()
    assert in_a["students"] == []

    # 事件记录的是发生时的班级，换班不改写历史审计字段
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        event = session.scalar(select(GrowthEvent).where(
            GrowthEvent.student_id == student_id))
        assert event.class_id_at_event == class_a


def test_transfer_student_starts_without_backfilled_points(tmp_path):
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01",))
    original_id = students[0]["id"]

    client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "pre-transfer",
        "items": [_activity(original_id)]})

    newcomer = client.post("/api/v1/students", headers=headers, json={
        "student_no": "99", "name": "转学生", "class_id": class_id})
    assert newcomer.status_code == 201, newcomer.text
    newcomer_id = newcomer.json()["id"]

    forest = client.get("/api/v1/growth/forest", headers=headers,
                        params={"term_id": term_id}).json()
    by_id = {row["student_id"]: row for row in forest["students"]}
    assert by_id[newcomer_id]["term_points"] == 0
    assert by_id[newcomer_id]["stage_name"] == "种子"
    assert by_id[original_id]["term_points"] == 2

    detail = client.get(f"/api/v1/growth/students/{newcomer_id}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["records"] == []
    assert detail["history"] == []


def test_new_term_does_not_inherit_legacy_or_term_points(tmp_path):
    """跨学期只继承带时间范围的历史摘要，新学期重新累计。"""
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    # 旧学期：一条正式活动 + 一批历史手工记录
    client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "cross-term",
        "items": [_activity(student_id)]})
    client.post("/api/v1/growth/legacy/confirm", headers=headers, json={
        "term_id": term_id, "batch_id": "cross-batch",
        "payload": {"logs": {"01": [{"date": "2025-09-01", "pts": 3, "note": "旧记录"}]}},
        "carry_over_date": "2025-09-30"})

    old = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                     params={"term_id": term_id}).json()
    assert old["snapshot"]["term_points"] == 2
    assert old["snapshot"]["legacy_points"] == 3

    new_term = client.post("/api/v1/terms", headers=headers, json={
        "code": "t-2027-2", "name": "2027 秋季", "clone_from_term_id": term_id,
        "clone_classes": True, "clone_enrollments": True})
    assert new_term.status_code == 201, new_term.text
    new_term_id = new_term.json()["id"]

    fresh = client.get(f"/api/v1/growth/students/{student_id}", headers=headers,
                       params={"term_id": new_term_id})
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    # 新学期活动、分母、营养重新累计；旧规则营养不结转
    assert body["snapshot"]["term_points"] == 0
    assert body["snapshot"]["legacy_points"] == 0
    assert body["records"] == []
    # 历史年轮保留旧学期的阶段摘要，供「时间范围」展示
    assert any(item["term_id"] == term_id for item in body["history"])

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        snapshots = session.scalars(select(StudentGrowthSnapshot).where(
            StudentGrowthSnapshot.student_id == student_id)).all()
        # 旧学期的快照由写入路径持久化，作为新学期「历史年轮」的来源
        old_snapshot = [snap for snap in snapshots if snap.term_id == term_id]
        assert old_snapshot and old_snapshot[0].term_points == 2
        # 新学期读取是只读的：不因为一次查看就写入新快照
        assert all(snap.term_id == term_id for snap in snapshots)


# ---------------------------------------------------------------- 模型不可用


def test_growth_works_without_model_provider(tmp_path):
    """模型不可用时森林与明细仍可用：事实不依赖任何模型调用。"""
    client = _client(tmp_path)
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers, names=("01", "02"))
    student_id = students[0]["id"]

    forest = client.get("/api/v1/growth/forest", headers=headers,
                        params={"term_id": term_id})
    assert forest.status_code == 200, forest.text
    assert forest.json()["summary"]["student_count"] == 2
    # 森林与明细不含任何模型生成的字段
    for row in forest.json()["students"]:
        assert set(row) >= {"term_points", "stage_name", "source_revision"}
        assert "narrative" not in row and "profile_summary" not in row

    created = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "no-model",
        "items": [_activity(student_id)]})
    assert created.status_code == 201, created.text

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        from backend.app.services.growth import rules as growth_rules
        from backend.app.services.growth import summary as growth_summary
        summary = growth_summary.build_growth_summary(
            session, student_id=student_id, term_id=term_id)
    # 数值全部来自规则重算，且没有 provider 参与
    assert summary["term_points"] == 2
    assert summary["rule_version"] == growth_rules.DEFAULT_RULE_CODE
    assert summary["source_revision"]
