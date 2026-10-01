"""教师级补录加分标准回归测试（每位老师各一套）。

覆盖：
- 教师档案惰性创建与出厂默认预设；
- 每位老师各一套预设、互不影响；
- 越界分值与未知类别被拒（不静默忽略）；
- 补录记录真实操作人，且改预设不改变历史记录分值；
- 切换教师后预设与署名同步切换；
- 多套标准分叉时显式报告 conflict；
- 至少保留一位教师；
- 指定 ``teacher_id`` 改别人的预设时不切换当前教师，未知 ``teacher_id`` 返回 404。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import GrowthEvent
from backend.app.services.growth import teachers as growth_teachers


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


def _preset_map(payload):
    """预设接口用 ``presets``，森林接口用 ``manual_presets``；统一成按类别索引。"""
    items = payload.get("presets") or payload.get("manual_presets") or []
    return {item["type"]: item for item in items}


def test_default_teacher_gets_factory_presets(tmp_path):
    """全新库经迁移后应有默认教师，预设等于后端出厂默认值（前端不再自带一份）。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()

    response = client.get("/api/v1/growth/teacher-presets", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["active_teacher"]["name"] == growth_teachers.DEFAULT_TEACHER_NAME
    presets = _preset_map(payload)
    # 出厂默认来自 rules.DEFAULT_EVENT_RULES，而不是前端硬编码。
    assert presets["task_completed"]["points"] == 2
    assert presets["spaced_review"]["points"] == 3
    assert presets["teacher_bonus"]["points"] == 1
    assert all(item["customized"] is False for item in payload["presets"])
    assert payload["max_points"] == growth_teachers.MAX_MANUAL_POINTS
    assert payload["conflict"]["conflict"] is False


def test_each_teacher_keeps_own_standard(tmp_path):
    """两位老师各改一套分值，互不影响——这是本补丁的核心诉求。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()

    created = client.post("/api/v1/growth/teachers", headers=headers,
                          json={"name": "王老师"})
    assert created.status_code == 201, created.text
    teachers = {item["name"]: item for item in created.json()["teachers"]}
    assert "王老师" in teachers

    # 王老师把「完成学习任务」定为 5 分、并把「课堂表现」从快捷区隐藏。
    saved = client.put("/api/v1/growth/teacher-presets", headers=headers, json={
        "presets": {"task_completed": {"points": 5},
                    "teacher_observation": {"visible": False}},
    })
    assert saved.status_code == 200, saved.text
    wang = _preset_map(saved.json())
    assert wang["task_completed"]["points"] == 5
    assert wang["task_completed"]["customized"] is True
    assert wang["teacher_observation"]["visible"] is False

    # 切回默认教师：标准应回到出厂值，不受王老师的改动影响。
    default_id = teachers[growth_teachers.DEFAULT_TEACHER_NAME]["id"]
    switched = client.post(f"/api/v1/growth/teachers/{default_id}/activate",
                           headers=headers)
    assert switched.status_code == 200, switched.text
    assert switched.json()["active_teacher"]["name"] == growth_teachers.DEFAULT_TEACHER_NAME
    default_presets = _preset_map(
        client.get("/api/v1/growth/teacher-presets", headers=headers).json())
    assert default_presets["task_completed"]["points"] == 2
    assert default_presets["teacher_observation"]["visible"] is True


def test_presets_are_validated_instead_of_silently_ignored(tmp_path):
    """越界分值与未知类别必须报错：静默忽略会让老师以为设置生效了。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()

    too_large = client.put("/api/v1/growth/teacher-presets", headers=headers, json={
        "presets": {"task_completed": {"points": growth_teachers.MAX_MANUAL_POINTS + 1}},
    })
    assert too_large.status_code == 422, too_large.text

    zero = client.put("/api/v1/growth/teacher-presets", headers=headers, json={
        "presets": {"task_completed": {"points": 0}},
    })
    assert zero.status_code == 422, zero.text

    unknown = client.put("/api/v1/growth/teacher-presets", headers=headers, json={
        "presets": {"exam_completed": {"points": 3}},
    })
    assert unknown.status_code == 422, unknown.text


def test_activity_records_operator_and_preset_change_keeps_history(tmp_path):
    """补录署名当前教师；事后改预设不得改变历史记录的分值。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    _, _, students = _seed_class(client, headers, names=("01",))
    student_id = students[0]["id"]

    created = client.post("/api/v1/growth/teachers", headers=headers, json={"name": "李老师"})
    li_id = {item["name"]: item["id"] for item in created.json()["teachers"]}["李老师"]
    client.post(f"/api/v1/growth/teachers/{li_id}/activate", headers=headers)

    response = client.post("/api/v1/growth/activities", headers=headers, json={
        "items": [{"student_id": student_id, "event_type": "task_completed",
                   "note": "完成课堂任务", "points": 2}],
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["teacher"]["name"] == "李老师"
    assert body["created"][0]["applied_points"] == 2

    # 李老师随后把该类别的预设调到 6 分：已入账的 2 分不受影响。
    client.put("/api/v1/growth/teacher-presets", headers=headers, json={
        "presets": {"task_completed": {"points": 6}},
    })
    detail = client.get(f"/api/v1/growth/students/{student_id}", headers=headers).json()
    record = next(item for item in detail["records"] if item["event_type"] == "task_completed")
    assert record["applied_points"] == 2
    assert record["actor"] == "李老师"
    assert detail["snapshot"]["term_points"] == 2

    # 事件账本里确实留下了操作人，便于事后核对「这条分是谁加的」。
    with client.app.state.session_factory() as session:
        event = session.get(GrowthEvent, record["event_id"])
        assert event.actor == "李老师"
        assert event.payload_json["teacher_id"] == li_id


def test_forest_reports_active_teacher_presets_and_conflict(tmp_path):
    """森林接口直接带回当前教师的预设，并在多套标准分叉时给出显式提示。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()
    _seed_class(client, headers)

    forest = client.get("/api/v1/growth/forest", headers=headers).json()
    assert forest["active_teacher"]["name"] == growth_teachers.DEFAULT_TEACHER_NAME
    assert _preset_map(forest)["task_completed"]["points"] == 2
    assert forest["standard_conflict"]["conflict"] is False

    # 第二位老师给出不同分值 → 营养值之间不再天然可比，必须显式暴露。
    created = client.post("/api/v1/growth/teachers", headers=headers, json={"name": "赵老师"})
    zhao_id = {item["name"]: item["id"] for item in created.json()["teachers"]}["赵老师"]
    client.put(f"/api/v1/growth/teacher-presets?teacher_id={zhao_id}", headers=headers, json={
        "presets": {"task_completed": {"points": 4}},
    })

    forest = client.get("/api/v1/growth/forest", headers=headers).json()
    conflict = forest["standard_conflict"]
    assert conflict["conflict"] is True
    assert conflict["teachers"] == 2
    assert "task_completed" in conflict["divergent_types"]


def test_cannot_remove_the_last_teacher(tmp_path):
    """至少保留一位教师，避免补录失去操作人。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()

    listed = client.get("/api/v1/growth/teachers", headers=headers).json()
    assert len(listed["teachers"]) == 1
    only_id = listed["teachers"][0]["id"]

    removed = client.delete(f"/api/v1/growth/teachers/{only_id}", headers=headers)
    assert removed.status_code == 409, removed.text
    assert client.get("/api/v1/growth/teachers", headers=headers).json()["teachers"]


def test_updating_another_teacher_preset_does_not_switch_active(tmp_path):
    """用 teacher_id 改别人的预设不应顺带切换当前教师。

    「改谁的设置」与「现在是谁在操作」是两件事：隐式切换会让后续补录的署名与
    实际标准错位，所以指定 teacher_id 时只写目标档案。
    """
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()

    created = client.post("/api/v1/growth/teachers", headers=headers,
                          json={"name": "陈老师", "activate": False})
    assert created.status_code == 201, created.text
    chen_id = {item["name"]: item["id"] for item in created.json()["teachers"]}["陈老师"]
    assert created.json()["active_teacher"]["name"] == growth_teachers.DEFAULT_TEACHER_NAME

    saved = client.put(f"/api/v1/growth/teacher-presets?teacher_id={chen_id}",
                       headers=headers, json={"presets": {"task_completed": {"points": 7}}})
    assert saved.status_code == 200, saved.text
    # 改的是陈老师，当前教师仍是默认教师；响应体描述的是「当前生效的标准」。
    assert saved.json()["active_teacher"]["name"] == growth_teachers.DEFAULT_TEACHER_NAME
    assert _preset_map(saved.json())["task_completed"]["points"] == 2

    # 陈老师自己的预设确实改好了，只是没被激活。
    client.post(f"/api/v1/growth/teachers/{chen_id}/activate", headers=headers)
    chen_presets = _preset_map(
        client.get("/api/v1/growth/teacher-presets", headers=headers).json())
    assert chen_presets["task_completed"]["points"] == 7


def test_updating_unknown_teacher_returns_404(tmp_path):
    """不存在的 teacher_id 必须 404，而不是悄悄落到当前教师身上。"""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    headers = auth_headers()

    response = client.put("/api/v1/growth/teacher-presets?teacher_id=99999", headers=headers,
                          json={"presets": {"task_completed": {"points": 3}}})
    assert response.status_code == 404, response.text

    # 当前教师的预设未被这次失败请求改动。
    default_presets = _preset_map(
        client.get("/api/v1/growth/teacher-presets", headers=headers).json())
    assert default_presets["task_completed"]["points"] == 2
