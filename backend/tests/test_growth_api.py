"""成长树 API 回归测试（方案 §8 首轮验收数据）。

覆盖：事件账本幂等、每日封顶、误录撤销（含重复撤销）、学期隔离、旧版导入
预览与确认（含重复导入、缺日期结转、整批撤销），以及空分/缺考不误奖。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import GrowthAward, GrowthEvent
from backend.app.services.growth import rules as growth_rules


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def _seed_class(client, headers, names=("01", "02")):
    """使用当前学期建立班级与学生（学生创建时会自动加入当前学期名单）。"""
    term = client.get("/api/v1/terms/current", headers=headers).json()
    term_id = term["id"]
    classroom = client.post("/api/v1/classes", headers=headers, json={"name": "711"})
    assert classroom.status_code == 201, classroom.text
    class_id = classroom.json()["id"]
    students = []
    for no in names:
        response = client.post("/api/v1/students", headers=headers, json={
            "student_no": no, "name": f"学生{no}", "class_id": class_id,
        })
        assert response.status_code == 201, response.text
        students.append(response.json())
    return term_id, class_id, students


def _activity(student_id, event_type="task_completed", note="完成课堂任务", **extra):
    return {"student_id": student_id, "event_type": event_type, "note": note, **extra}


def test_forest_reflects_recorded_activity(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, class_id, students = _seed_class(client, headers)
    first, second = students

    response = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id,
        "request_id": "req-1",
        "items": [_activity(first["id"]), _activity(first["id"])],
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["created"]) == 2
    assert body["skipped"] == []

    forest = client.get("/api/v1/growth/forest", headers=headers,
                        params={"term_id": term_id}).json()
    assert forest["summary"]["student_count"] == 2
    by_id = {row["student_id"]: row for row in forest["students"]}
    # 每天最多两项 task_completed，各 +2 → 4 分
    assert by_id[first["id"]]["term_points"] == 4
    assert by_id[first["id"]]["week_points"] == 4
    assert by_id[first["id"]]["stage_name"] == "种子"
    assert by_id[second["id"]]["term_points"] == 0

    detail = client.get(f"/api/v1/growth/students/{first['id']}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 4
    assert len(detail["records"]) == 2
    for record in detail["records"]:
        assert record["proposed_points"] == 2
        assert record["applied_points"] == 2
        assert record["cap_reason"] == "none"
        assert record["reversible"] is True
    assert detail["pending_corrections"] == []


def test_daily_category_cap_keeps_evidence_but_limits_points(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student = students[0]

    items = [_activity(student["id"]) for _ in range(4)]
    response = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "req-cap", "items": items})
    assert response.status_code == 201, response.text

    detail = client.get(f"/api/v1/growth/students/{student['id']}", headers=headers,
                        params={"term_id": term_id}).json()
    # 4 条记录全部保留（原始证据不因封顶丢失），但只有前两条入账
    assert len(detail["records"]) == 4
    assert detail["snapshot"]["term_points"] == 4
    reasons = sorted(record["cap_reason"] for record in detail["records"])
    assert reasons == ["category_daily_awards", "category_daily_awards", "none", "none"]


def test_duplicate_request_does_not_double_count(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student = students[0]
    payload = {"term_id": term_id, "request_id": "req-retry",
               "items": [_activity(student["id"])]}

    first = client.post("/api/v1/growth/activities", headers=headers, json=payload).json()
    second = client.post("/api/v1/growth/activities", headers=headers, json=payload).json()
    assert len(first["created"]) == 1
    assert second["created"] == []
    assert second["skipped"][0]["reason"].startswith("重复请求")

    detail = client.get(f"/api/v1/growth/students/{student['id']}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 2
    assert len(detail["records"]) == 1


def test_reversal_restores_points_and_blocks_double_reversal(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student = students[0]

    created = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "req-rev",
        "items": [_activity(student["id"], "spaced_review", "间隔复习达标")]}).json()
    event_id = created["created"][0]["event_id"]

    before = client.get(f"/api/v1/growth/students/{student['id']}", headers=headers,
                        params={"term_id": term_id}).json()
    assert before["snapshot"]["term_points"] == 3

    reversed_response = client.post(f"/api/v1/growth/events/{event_id}/reverse",
                                    headers=headers,
                                    json={"term_id": term_id, "reason": "误录"})
    assert reversed_response.status_code == 201, reversed_response.text
    after = reversed_response.json()["snapshot"]
    assert after["term_points"] == 0

    again = client.post(f"/api/v1/growth/events/{event_id}/reverse", headers=headers,
                        json={"term_id": term_id, "reason": "再撤一次"})
    assert again.status_code == 409


def test_manual_entry_requires_reason_and_cannot_inflate_points(tmp_path):
    """空分/缺考不产生奖励；补录必须有事由，且不能手工刷分。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student = students[0]

    # 空事由被拒绝：补录必须关联具体事由，避免空记录刷分
    blank = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "req-blank",
        "items": [{"student_id": student["id"], "event_type": "task_completed", "note": ""}]})
    assert blank.status_code == 422

    # 非课堂观察类型忽略自定义点数，固定按规则 +2
    inflated = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "req-inflate",
        "items": [{"student_id": student["id"], "event_type": "task_completed",
                   "note": "完成课堂任务", "points": 2}]})
    assert inflated.status_code == 201, inflated.text
    detail = client.get(f"/api/v1/growth/students/{student['id']}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 2
    assert detail["records"][0]["proposed_points"] == 2

    # 课堂观察可显式给 1–2 分，超出范围被 schema 拒绝
    out_of_range = client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "req-obs-bad",
        "items": [{"student_id": student["id"], "event_type": "teacher_observation",
                   "note": "课堂主动回答", "points": 5}]})
    assert out_of_range.status_code == 422


def test_student_without_evidence_stays_at_seed(tmp_path):
    """空分/缺考不奖励：没有证据的学生保持种子阶段、0 营养。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student = students[0]

    detail = client.get(f"/api/v1/growth/students/{student['id']}", headers=headers,
                        params={"term_id": term_id}).json()
    assert detail["snapshot"]["term_points"] == 0
    assert detail["snapshot"]["stage_name"] == "种子"
    assert detail["records"] == []
    # 五个分支在无证据时明确标注，而不是补零当作能力值
    for dimension in detail["snapshot"]["dimensions"].values():
        assert dimension["status"] in {"no_evidence", "insufficient_comparable_history"}


def test_scope_isolation_across_terms(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student = students[0]
    client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "req-scope",
        "items": [_activity(student["id"])]})

    other = client.post("/api/v1/terms", headers=headers,
                        json={"code": "t-2027-1", "name": "2027 春季",
                              "clone_classes": False, "clone_enrollments": False})
    assert other.status_code == 201, other.text
    other_term_id = other.json()["id"]
    assert other_term_id != term_id

    # 新学期没有该学生，森林为空且明细 404，不能继承旧学期积分
    forest = client.get("/api/v1/growth/forest", headers=headers,
                        params={"term_id": other_term_id}).json()
    assert forest["students"] == []
    detail = client.get(f"/api/v1/growth/students/{student['id']}", headers=headers,
                        params={"term_id": other_term_id})
    assert detail.status_code == 404
    # 原学期积分仍在
    original = client.get(f"/api/v1/growth/students/{student['id']}", headers=headers,
                          params={"term_id": term_id}).json()
    assert original["snapshot"]["term_points"] == 2


def test_legacy_import_preview_confirm_and_batch_reverse(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01", "02"))
    first, second = students

    legacy = {
        "source": "windows-forest",
        "source_term": "2025-2026-1",
        "batch_label": "迁移 2026-09",
        "logs": {
            "01": [
                {"date": "2025-09-01", "pts": 3, "note": "作业按时完成"},
                {"date": "2025-09-01", "pts": 3, "note": "作业按时完成"},  # 重复候选
                {"date": None, "pts": 2, "note": "无日期记录"},
            ],
            "02": [{"date": "2025-09-02", "pts": -5, "note": "作业未交"}],
        },
        "legacy_totals": {"01": 6, "02": -5},
    }

    preview = client.post("/api/v1/growth/legacy/preview", headers=headers,
                          json={"term_id": term_id, "payload": legacy})
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["totals"]["students_mapped"] == 2
    assert preview_body["totals"]["undated_records"] == 1
    types = {item["type"] for item in preview_body["anomalies"]}
    assert {"duplicate_candidates", "missing_date"} <= types
    by_key = {row["student_key"]: row for row in preview_body["students"]}
    assert by_key["01"]["old_total"] == 6
    assert by_key["01"]["difference"] == 6 - 8  # 8 = 3+3+2

    confirm = client.post("/api/v1/growth/legacy/confirm", headers=headers, json={
        "term_id": term_id, "payload": legacy, "batch_id": "batch-A",
        "carry_over_date": "2025-09-30"})
    assert confirm.status_code == 201, confirm.text
    confirm_body = confirm.json()
    assert confirm_body["batch_id"] == "batch-A"
    # 01: 两条带日期 + 一条聚合缺日期 = 3；02: 1 条
    assert len(confirm_body["created"]) == 4

    detail = client.get(f"/api/v1/growth/students/{first['id']}", headers=headers,
                        params={"term_id": term_id}).json()
    # 历史营养（旧规则）计入 legacy_points，不进入本学期营养
    assert detail["snapshot"]["legacy_points"] == 8
    assert detail["snapshot"]["term_points"] == 0

    # 重复导入同一批次：不新增事件
    again = client.post("/api/v1/growth/legacy/confirm", headers=headers, json={
        "term_id": term_id, "payload": legacy, "batch_id": "batch-A",
        "carry_over_date": "2025-09-30"}).json()
    assert again["created"] == []
    assert all(item["reason"] == "同一批次已导入" for item in again["skipped"])

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        count = session.scalar(select(func.count(GrowthEvent.id)).where(
            GrowthEvent.source_id == "batch-A"))
        assert count == 4

    # 整批撤销：撤销事件追加，原记录保留
    reverse = client.post("/api/v1/growth/legacy/batch-A/reverse", headers=headers,
                          json={"term_id": term_id, "reason": "整批回滚"})
    assert reverse.status_code == 200, reverse.text
    assert reverse.json()["reversed_count"] == 4

    detail_after = client.get(f"/api/v1/growth/students/{first['id']}", headers=headers,
                              params={"term_id": term_id}).json()
    assert detail_after["snapshot"]["legacy_points"] == 0


def test_legacy_import_requires_confirmed_mapping(tmp_path):
    """无法映射的学号进入人工核对清单，不按姓名猜。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, _students = _seed_class(client, headers, names=("01",))

    preview = client.post("/api/v1/growth/legacy/preview", headers=headers, json={
        "term_id": term_id,
        "payload": {"logs": {"99": [{"date": "2025-09-01", "pts": 3, "note": "未知来源"}]}},
    }).json()
    assert preview["totals"]["students_mapped"] == 0
    assert preview["totals"]["students_unmapped"] == 1
    assert preview["unmapped_students"][0]["student_key"] == "99"


def test_growth_awards_are_rebuildable_and_rule_versioned(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    term_id, _class_id, students = _seed_class(client, headers, names=("01",))
    student = students[0]
    client.post("/api/v1/growth/activities", headers=headers, json={
        "term_id": term_id, "request_id": "req-award",
        "items": [_activity(student["id"], "correction_verified", "订正并确认")]})

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        awards = list(session.scalars(select(GrowthAward)))
        assert awards
        assert all(award.rule_version == growth_rules.DEFAULT_RULE_CODE for award in awards)
        assert sum(award.applied_points for award in awards) == 2
