"""按题型批量上传小分（``PUT /api/v1/exams/{id}/item-scores``）的契约回归。

这组测试锁住四条不肯让步的性质：

1. 定位靠「题型 + 题号」，题型名同时接受 ``question_type`` 与 ``section_name``；
2. 校验是整批的——任何一行不合格都整批取消，绝不写一半；
3. 本批次没带到的题目保留已有小分，不写空、不补零；
4. 总分不被上传动作改写，教师已确认的小分默认不被覆盖。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import (
    ExamPaperVersion,
    ExamQuestion,
    ExamScore,
    StudentItemResult,
)


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def seed(client, *, total_scores=(45, 38), attendance=("present", "present")):
    """建一个班、两名学生、一场按题型考试，以及一份已确认试卷结构。

    听力：1 题（10 分）、2 题（20 分）、4 题含 a/b 两个小题（各 5 分）共 40 分；
    阅读理解：3 题（20 分）。听力 4 题故意拆成两个小题，用于验证歧义定位。
    """
    headers = auth_headers()
    class_id = client.post("/api/v1/classes", headers=headers, json={"name": "711"}).json()["id"]
    students = []
    for index, (student_no, name) in enumerate(((("01"), "张三"), (("02"), "李四"))):
        students.append(client.post(
            "/api/v1/students", headers=headers,
            json={"student_no": student_no, "name": name, "class_id": class_id},
        ).json())
    exam = client.post(
        "/api/v1/exams", headers=headers,
        json={
            "name": "期中考试", "full_score": 60, "exam_type": "question_type",
            "dimensions": [
                {"code": "listening", "name": "听力", "max_score": 40, "position": 1},
                {"code": "reading", "name": "阅读理解", "max_score": 20, "position": 2},
            ],
        },
    ).json()
    with client.app.state.session_factory() as session:
        paper = ExamPaperVersion(exam_id=exam["id"], version=1, status="confirmed", full_score=60)
        session.add(paper)
        session.flush()
        for question_no, sub_no, question_type, max_score in (
            ("1", None, "听力", 10),
            ("2", None, "听力", 20),
            ("3", None, "阅读理解", 20),
            ("4", "a", "听力", 5),
            ("4", "b", "听力", 5),
        ):
            session.add(ExamQuestion(
                paper_version_id=paper.id, question_no=question_no, sub_question_no=sub_no,
                question_type=question_type, section_name=None, max_score=max_score,
            ))
        for student, total, status in zip(students, total_scores, attendance):
            session.add(ExamScore(
                exam_id=exam["id"], student_id=student["id"], total_score=total,
                class_id_at_exam=class_id, attendance_status=status,
            ))
        session.commit()
    return headers, class_id, exam, students


def item_scores(client, headers, exam_id, rows, **extra):
    return client.put(
        f"/api/v1/exams/{exam_id}/item-scores", headers=headers,
        json={"rows": rows, **extra},
    )


def stored_item_scores(client, exam_id):
    with client.app.state.session_factory() as session:
        return {
            (item.student_id, item.question_id): (item.score, item.score_rate, item.correct)
            for item in session.query(StudentItemResult).filter(
                StudentItemResult.exam_id == exam_id
            )
        }


def test_bulk_upload_writes_item_scores_and_keeps_total_untouched(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, students = seed(client)

    response = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 10},
        {"student_no": "01", "question_type": "听力", "question_no": "2", "score": 18},
        {"student_no": "01", "question_type": "阅读理解", "question_no": "3", "score": 15},
        {"student_id": students[1]["id"], "question_type": "听力", "question_no": "1", "score": 8},
        {"student_id": students[1]["id"], "question_type": "阅读理解", "question_no": "3", "score": 20},
    ], note="期中考试小分表")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["written"] == 5
    assert body["student_count"] == 2
    assert body["paper_status"] == "confirmed"
    assert body["paper_version"] == 1
    assert body["question_types"] == ["听力", "阅读理解"]
    assert body["skipped"] == []
    assert body["overwritten_overrides"] == 0
    # 总分由 PUT /scores 负责，上传小分不得顺手改写它。
    assert body["total_score_missing_count"] == 0

    # 题型回显：听力 4 道小题都没录满，平均分保持为 None 而不是被补零压低。
    listening, reading = body["sections"]
    assert listening == {
        "section_name": "听力", "max_score": 40.0, "expected_items": 8, "scored_items": 3,
        "complete_student_count": 0, "average_score": None,
    }
    # 阅读理解只有 1 道题，两名学生都录了，才有平均分。
    assert reading["section_name"] == "阅读理解"
    assert reading["max_score"] == 20.0
    assert reading["expected_items"] == 2
    assert reading["scored_items"] == 2
    assert reading["complete_student_count"] == 2
    assert reading["average_score"] == 17.5

    # 派生字段：score_rate 与 correct 必须同步，否则知识点/错因分析会读错。
    results = client.get(
        f"/api/v1/exams/{exam['id']}/students/{students[0]['id']}/item-results",
        headers=headers,
    ).json()
    by_no = {row["question_no"]: row for row in results}
    assert by_no["1"]["score"] == 10 and by_no["1"]["score_rate"] == 1.0
    assert by_no["1"]["correct"] is True
    assert by_no["2"]["score"] == 18 and by_no["2"]["score_rate"] == 0.9
    assert by_no["2"]["correct"] is False
    assert by_no["2"]["teacher_override"] is True
    assert by_no["2"]["override_note"] == "期中考试小分表"
    assert by_no["3"]["score_rate"] == 0.75
    assert by_no["4"]["score"] is None

    details = client.get(
        f"/api/v1/exams/{exam['id']}/students/{students[0]['id']}/score-details",
        headers=headers,
    ).json()
    assert details["total_score"] == 45  # 上传小分没有改总分
    assert details["detail_status"] == "partial"
    assert details["scored_items"] == 3
    assert {row["section_name"]: row["score"] for row in details["section_scores"]} == {
        "听力": 28.0, "阅读理解": 15.0,
    }

    # 写入以小分来源留痕，便于区分「上次上传」与「教师单题修正」。
    with client.app.state.session_factory() as session:
        stored = session.query(StudentItemResult).filter(
            StudentItemResult.exam_id == exam["id"]
        ).all()
        assert {item.source_record_id for item in stored} == {"item-scores:1"}


def test_upload_is_all_or_nothing_when_any_row_is_invalid(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, _students = seed(client)

    unknown_type = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 10},
        {"student_no": "01", "question_type": "物理", "question_no": "1", "score": 10},
    ])
    assert unknown_type.status_code == 422
    detail = unknown_type.json()["detail"]
    assert detail["code"] == "item_score_rows_invalid"
    assert [item["reason"] for item in detail["problems"]] == ["question_not_found"]
    assert "可用题型" in detail["problems"][0]["message"]
    assert stored_item_scores(client, exam["id"]) == {}  # 一行都没落库

    # 题型写对了、题号写错时，要直接告诉教师这个题型有哪些题号可填。
    wrong_number = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "4-a", "score": 5},
    ])
    assert wrong_number.status_code == 422
    message = wrong_number.json()["detail"]["problems"][0]["message"]
    assert "可用题号" in message
    assert "4-a" in message and "1" in message

    too_high = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 10.5},
    ])
    assert too_high.status_code == 422
    assert too_high.json()["detail"]["problems"][0]["reason"] == "score_exceeds_max"

    unknown_student = item_scores(client, headers, exam["id"], [
        {"student_no": "99", "question_type": "听力", "question_no": "1", "score": 5},
    ])
    assert unknown_student.status_code == 422
    assert unknown_student.json()["detail"]["problems"][0]["reason"] == "student_not_found"

    assert stored_item_scores(client, exam["id"]) == {}


def test_absent_student_and_missing_score_row_are_rejected(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, class_id, exam, students = seed(client, attendance=("absent", "present"))

    absent = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 10},
    ])
    assert absent.status_code == 422
    assert absent.json()["detail"]["problems"][0]["reason"] == "attendance_not_present"

    # 第二名学生先删掉成绩记录：没有成绩记录时整批拒绝，不顺手造一条只有小分的记录。
    with client.app.state.session_factory() as session:
        score = session.query(ExamScore).filter(
            ExamScore.exam_id == exam["id"],
            ExamScore.student_id == students[1]["id"],
        ).one()
        session.delete(score)
        session.commit()
    missing = item_scores(client, headers, exam["id"], [
        {"student_id": students[1]["id"], "question_type": "听力", "question_no": "1", "score": 10},
    ])
    assert missing.status_code == 422
    assert missing.json()["detail"]["problems"][0]["reason"] == "score_row_missing"
    with client.app.state.session_factory() as session:
        assert session.query(ExamScore).filter(ExamScore.exam_id == exam["id"]).count() == 1
    assert class_id is not None


def test_upload_without_confirmed_paper_is_rejected(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    exam_id = client.post(
        "/api/v1/exams", headers=headers, json={"name": "随堂测", "full_score": 100},
    ).json()["id"]
    response = item_scores(client, headers, exam_id, [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 5},
    ])
    assert response.status_code == 404
    assert "试卷结构" in response.json()["detail"]


def test_unlisted_questions_keep_their_existing_scores(tmp_path):
    """不完整批次不得把已有小分写成空——与 school_sync 同一口径。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, students = seed(client)
    first = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 10},
        {"student_no": "01", "question_type": "听力", "question_no": "2", "score": 16},
    ])
    assert first.status_code == 200, first.text

    # 第二批只带第 1 题：第 2 题的 16 分必须原样保留。
    second = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 6},
    ], overwrite_teacher_override=True)
    assert second.status_code == 200, second.text
    assert second.json()["written"] == 1

    results = client.get(
        f"/api/v1/exams/{exam['id']}/students/{students[0]['id']}/item-results",
        headers=headers,
    ).json()
    by_no = {row["question_no"]: row["score"] for row in results}
    assert by_no["1"] == 6
    assert by_no["2"] == 16
    # 没带到的题目依然是「未记录」，不是 0 分。
    assert by_no["3"] is None


def test_previous_upload_and_teacher_fix_are_protected_by_default(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, students = seed(client)
    row = {"student_no": "01", "question_type": "阅读理解", "question_no": "3", "score": 12}
    assert item_scores(client, headers, exam["id"], [row]).status_code == 200

    # 重传同一份表：默认不覆盖自己上一次的上传，但会明确告诉教师原因。
    again = item_scores(client, headers, exam["id"], [row])
    assert again.status_code == 200, again.text
    assert again.json()["written"] == 0
    assert again.json()["skipped"] == [{
        "student_no": "01", "question_type": "阅读理解", "question_no": "3",
        "reason": "previous_upload",
    }]

    forced = item_scores(client, headers, exam["id"], [
        {**row, "score": 14},
    ], overwrite_teacher_override=True)
    assert forced.status_code == 200, forced.text
    assert forced.json()["written"] == 1
    assert forced.json()["overwritten_overrides"] == 1

    # 教师单题修正过的分数同样默认受保护。
    question_id = next(
        item["question_id"] for item in client.get(
            f"/api/v1/exams/{exam['id']}/students/{students[0]['id']}/item-results",
            headers=headers,
        ).json() if item["question_no"] == "3"
    )
    patched = client.patch(
        f"/api/v1/exams/{exam['id']}/students/{students[0]['id']}/item-results/{question_id}",
        headers=headers, json={"score": 9, "override_note": "教师复核"},
    )
    assert patched.status_code == 200, patched.text

    blocked = item_scores(client, headers, exam["id"], [{**row, "score": 20}])
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["written"] == 0
    assert blocked.json()["skipped"][0]["reason"] == "score_changed"
    assert blocked.json()["skipped"][0]["existing_score"] == 9
    assert blocked.json()["skipped"][0]["incoming_score"] == 20

    overridden = item_scores(client, headers, exam["id"], [
        {**row, "score": 20},
    ], overwrite_teacher_override=True)
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["written"] == 1
    results = client.get(
        f"/api/v1/exams/{exam['id']}/students/{students[0]['id']}/item-results",
        headers=headers,
    ).json()
    assert {item["question_no"]: item["score"] for item in results}["3"] == 20


def test_ambiguous_question_no_needs_sub_question_no(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, _students = seed(client)

    ambiguous = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "4", "score": 5},
    ])
    assert ambiguous.status_code == 422
    assert ambiguous.json()["detail"]["problems"][0]["reason"] == "question_ambiguous"

    resolved = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "4",
         "sub_question_no": "a", "score": 4},
        {"student_no": "01", "question_type": "听力", "question_no": "4",
         "sub_question_no": "b", "score": 5},
    ])
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["written"] == 2


def test_duplicate_rows_in_one_batch_are_rejected(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, _students = seed(client)
    duplicated = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 10},
        {"student_no": "01", "question_type": "听力", "question_no": "1", "score": 8},
    ])
    assert duplicated.status_code == 422


def test_section_name_is_accepted_as_question_type(tmp_path):
    """视觉识别把题型写进 question_type，学校同步把卷面小节写进 section_name。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, _students = seed(client)
    with client.app.state.session_factory() as session:
        paper = session.query(ExamPaperVersion).filter(
            ExamPaperVersion.exam_id == exam["id"]
        ).one()
        question = session.query(ExamQuestion).filter(
            ExamQuestion.paper_version_id == paper.id,
            ExamQuestion.question_no == "2",
        ).one()
        question.section_name = "第二部分 听力"
        question.question_type = None
        session.commit()

    response = item_scores(client, headers, exam["id"], [
        {"student_no": "01", "question_type": "第二部分 听力", "question_no": "2", "score": 15},
    ])
    assert response.status_code == 200, response.text
    assert response.json()["written"] == 1
    assert response.json()["sections"][0]["section_name"] == "第二部分 听力"


def test_draft_paper_requires_explicit_opt_in(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, _students = seed(client)
    with client.app.state.session_factory() as session:
        paper = session.query(ExamPaperVersion).filter(
            ExamPaperVersion.exam_id == exam["id"]
        ).one()
        paper.status = "draft"
        paper.version = 2
        session.commit()

    rows = [{"student_no": "01", "question_type": "听力", "question_no": "1", "score": 10}]
    assert item_scores(client, headers, exam["id"], rows).status_code == 404
    draft = item_scores(client, headers, exam["id"], rows, include_draft=True)
    assert draft.status_code == 200, draft.text
    assert draft.json()["paper_status"] == "draft"
    assert draft.json()["written"] == 1


def test_question_metrics_exposes_the_upload_skeleton(tmp_path):
    """前端上传界面靠 question-metrics 给出「题型 / 题号 / 小题号 / 满分」骨架。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers, _class_id, exam, _students = seed(client)

    questions = client.get(
        f"/api/v1/exams/{exam['id']}/question-metrics", headers=headers,
    ).json()["questions"]
    assert [
        (item["question_type"], item["question_no"], item["sub_question_no"], item["max_score"])
        for item in questions
    ] == [
        ("听力", "1", None, 10.0),
        ("听力", "2", None, 20.0),
        ("阅读理解", "3", None, 20.0),
        ("听力", "4", "a", 5.0),
        ("听力", "4", "b", 5.0),
    ]

    # 没有已确认试卷结构时不返回骨架（而不是返回空壳让学生猜）。
    bare_exam_id = client.post(
        "/api/v1/exams", headers=headers, json={"name": "无结构考试", "full_score": 50},
    ).json()["id"]
    assert client.get(
        f"/api/v1/exams/{bare_exam_id}/question-metrics", headers=headers,
    ).json()["questions"] == []
