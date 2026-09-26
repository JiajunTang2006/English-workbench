"""回归（评审 P2）：草稿试卷不得被当作正式事实。

同一场考试可能只有 draft 试卷版本（试卷录入后尚未确认）。审核界面与正式诊断
必须对「能不能读」给出一致答案：旧实现里 score-details 回退到草稿返回
draft/complete，而 item-results 只认已确认试卷返回 404，形成事实分叉。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import (
    Exam,
    ExamPaperVersion,
    ExamQuestion,
    ExamScore,
    StudentItemResult,
)


def _seed_draft_only_exam(client):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    class_id = client.post("/api/v1/classes", headers=headers,
                           json={"name": "711"}).json()["id"]
    student_id = client.post(
        "/api/v1/students", headers=headers,
        json={"student_no": "01", "name": "张三", "class_id": class_id},
    ).json()["id"]
    exam_id = client.post("/api/v1/exams", headers=headers,
                          json={"name": "随堂测", "full_score": 100}).json()["id"]

    with client as active:
        session = active.app.state.session_factory()
        try:
            term_id = session.get(Exam, exam_id).term_id
            paper = ExamPaperVersion(exam_id=exam_id, version=1, status="draft",
                                     full_score=100)
            session.add(paper)
            session.flush()
            question = ExamQuestion(paper_version_id=paper.id, question_no="1",
                                    max_score=10)
            session.add(question)
            session.add(ExamScore(exam_id=exam_id, student_id=student_id,
                                  total_score=8, class_id_at_exam=class_id,
                                  attendance_status="present"))
            session.flush()
            session.add(StudentItemResult(exam_id=exam_id, student_id=student_id,
                                          question_id=question.id, score=8,
                                          attendance_status="present"))
            session.commit()
        finally:
            session.close()
    return headers, term_id, exam_id, student_id


def test_draft_only_paper_is_not_formal_fact(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers, term_id, exam_id, student_id = _seed_draft_only_exam(client)

        # 正式诊断（默认）：只看已确认试卷，没有就明确 no_paper。
        details = client.get(
            f"/api/v1/exams/{exam_id}/students/{student_id}/score-details"
            f"?term_id={term_id}", headers=headers)
        assert details.status_code == 200, details.text
        body = details.json()
        assert body["paper_status"] == "missing"
        assert body["detail_status"] == "no_paper"
        assert body["item_scores"] == []

        # 逐题接口保持同一口径：没有已确认试卷 → 404。
        items = client.get(
            f"/api/v1/exams/{exam_id}/students/{student_id}/item-results"
            f"?term_id={term_id}", headers=headers)
        assert items.status_code == 404, items.text

        # 审核界面显式请求草稿时才能读到，并带出 paper_status 供标注。
        draft = client.get(
            f"/api/v1/exams/{exam_id}/students/{student_id}/score-details"
            f"?term_id={term_id}&include_draft=true", headers=headers)
        assert draft.status_code == 200, draft.text
        draft_body = draft.json()
        assert draft_body["paper_status"] == "draft"
        assert draft_body["detail_status"] == "complete"
        assert draft_body["scored_items"] == 1


def test_confirmed_version_wins_over_newer_draft(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        headers, term_id, exam_id, student_id = _seed_draft_only_exam(client)

        with client as active:
            session = active.app.state.session_factory()
            try:
                paper = session.query(ExamPaperVersion).filter_by(exam_id=exam_id).one()
                paper.status = "confirmed"
                session.add(ExamPaperVersion(exam_id=exam_id, version=2, status="draft",
                                             full_score=100))
                session.commit()
            finally:
                session.close()

        details = client.get(
            f"/api/v1/exams/{exam_id}/students/{student_id}/score-details"
            f"?term_id={term_id}", headers=headers).json()
        # 已确认版本永远优先于更新的草稿，正式结论不被未确认结构改写。
        assert details["paper_status"] == "confirmed"
        assert details["paper_version"] == 1
