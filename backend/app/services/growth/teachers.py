"""教师档案与「每位老师自己那套补录加分标准」。

设计要点（与项目既有约定一致）：

- **预设不进入计分引擎**。补录仍然是 ``teacher_confirmed_v1``：教师确认多少分就
  入账多少分。这里的预设只决定快捷按钮显示什么、自定义输入框默认填什么，
  因此调整预设不会让任何历史记录的口径发生变化，也不需要新建规则版本。
- **默认值只有一份**。各类别的出厂默认分值取自 ``rules.DEFAULT_EVENT_RULES``，
  教师档案只存「覆盖了哪几项」。前端不再持有第二份分值表。
- **可选项由规则版本决定**。只有当前学期规则版本里存在、且属于手工补录集合的
  事件类型才会出现，避免旧学期（如 growth-v3）重新出现已下线的手工类别。
- **分值有护栏**。单次补录分值限制在 1..``MAX_MANUAL_POINTS``，避免个别老师把
  单项设到过高后，阶段阈值（30/80/160/270/400/560）失去区分度。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ...models import AppSetting, TeacherProfile
from . import events as growth_events
from . import rules


DEFAULT_TEACHER_NAME = "默认教师"
ACTIVE_TEACHER_KEY = "active_teacher_id"
# 单次手工补录的分值上限：与阶段阈值配合，避免单项分值把阶段拉开到失真。
MAX_MANUAL_POINTS = 10
MAX_TEACHER_NAME_LENGTH = 32


class TeacherScopeError(ValueError):
    """教师档案操作失败（重名、删除最后一位教师等）。"""


def _ordered_manual_types(rule) -> list[str]:
    """当前规则版本里可手工补录的事件类型，按规则定义顺序返回。"""
    configured = rule.event_rules_json or rules.DEFAULT_EVENT_RULES
    return [
        key for key in configured
        if key in rules.MANUAL_EVENT_TYPES and not configured[key].get("legacy")
    ]


def _default_points(rule, event_type: str) -> int:
    configured = (rule.event_rules_json or rules.DEFAULT_EVENT_RULES).get(event_type) or {}
    try:
        points = int(configured.get("points") or 1)
    except (TypeError, ValueError):
        points = 1
    return max(1, min(MAX_MANUAL_POINTS, points))


def resolve_presets(session, *, teacher: TeacherProfile | None = None,
                    term_id: int | None = None) -> list[dict[str, Any]]:
    """当前教师生效的补录预设：出厂默认 + 该教师的覆盖。

    每项包含 ``points``（快捷按钮与默认输入值）、``visible``（是否出现在快捷区）、
    ``default_points``（出厂值，供界面标注「已自定义」）与 ``customized``。
    """
    rule = growth_events.read_rule_version(session, term_id=term_id)
    overrides = (teacher.presets_json or {}) if teacher is not None else {}
    presets: list[dict[str, Any]] = []
    for event_type in _ordered_manual_types(rule):
        configured = (rule.event_rules_json or {}).get(event_type) or {}
        default_points = _default_points(rule, event_type)
        entry = overrides.get(event_type) if isinstance(overrides, dict) else None
        points = default_points
        visible = True
        customized = False
        if isinstance(entry, dict):
            raw = entry.get("points")
            if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= MAX_MANUAL_POINTS:
                points = raw
                customized = points != default_points
            if isinstance(entry.get("visible"), bool):
                visible = entry["visible"]
        presets.append({
            "type": event_type,
            "label": str(configured.get("label") or event_type),
            "points": points,
            "default_points": default_points,
            "visible": visible,
            "customized": customized,
        })
    return presets


def _active_teacher_id(session) -> int | None:
    setting = session.get(AppSetting, ACTIVE_TEACHER_KEY)
    if setting is None:
        return None
    value = setting.value_json
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _set_active_teacher_id(session, teacher_id: int) -> None:
    setting = session.get(AppSetting, ACTIVE_TEACHER_KEY)
    if setting is None:
        session.add(AppSetting(key=ACTIVE_TEACHER_KEY, value_json=teacher_id))
    else:
        setting.value_json = teacher_id
    session.flush()


def get_teacher(session, teacher_id: int) -> TeacherProfile | None:
    return session.get(TeacherProfile, teacher_id)


def list_teachers(session) -> list[TeacherProfile]:
    return list(session.scalars(select(TeacherProfile).order_by(TeacherProfile.id)))


def get_active_teacher(session) -> TeacherProfile | None:
    """只读地取当前教师；不存在时返回 ``None``（GET 不产生副作用）。"""
    teacher_id = _active_teacher_id(session)
    if teacher_id is not None:
        teacher = session.get(TeacherProfile, teacher_id)
        if teacher is not None:
            return teacher
    return session.scalar(select(TeacherProfile).order_by(TeacherProfile.id).limit(1))


def ensure_active_teacher(session) -> TeacherProfile:
    """写入路径使用：没有教师档案时创建默认教师并设为当前。"""
    teacher = get_active_teacher(session)
    if teacher is None:
        teacher = TeacherProfile(name=DEFAULT_TEACHER_NAME, presets_json={})
        session.add(teacher)
        session.flush()
    if _active_teacher_id(session) != teacher.id:
        _set_active_teacher_id(session, teacher.id)
    return teacher


def _clean_name(name: Any) -> str:
    cleaned = " ".join(str(name or "").split())[:MAX_TEACHER_NAME_LENGTH]
    if not cleaned:
        raise TeacherScopeError("教师姓名不能为空")
    return cleaned


def create_teacher(session, *, name: Any, presets: dict[str, Any] | None = None) -> TeacherProfile:
    cleaned = _clean_name(name)
    if session.scalar(select(TeacherProfile).where(TeacherProfile.name == cleaned)):
        raise TeacherScopeError("已存在同名教师")
    teacher = TeacherProfile(name=cleaned, presets_json=dict(presets or {}))
    session.add(teacher)
    session.flush()
    return teacher


def rename_teacher(session, *, teacher_id: int, name: Any) -> TeacherProfile:
    teacher = session.get(TeacherProfile, teacher_id)
    if teacher is None:
        raise TeacherScopeError("教师不存在")
    cleaned = _clean_name(name)
    clash = session.scalar(select(TeacherProfile).where(
        TeacherProfile.name == cleaned, TeacherProfile.id != teacher_id))
    if clash is not None:
        raise TeacherScopeError("已存在同名教师")
    teacher.name = cleaned
    session.flush()
    return teacher


def activate_teacher(session, *, teacher_id: int) -> TeacherProfile:
    teacher = session.get(TeacherProfile, teacher_id)
    if teacher is None:
        raise TeacherScopeError("教师不存在")
    _set_active_teacher_id(session, teacher.id)
    return teacher


def delete_teacher(session, *, teacher_id: int) -> None:
    teacher = session.get(TeacherProfile, teacher_id)
    if teacher is None:
        raise TeacherScopeError("教师不存在")
    if len(list_teachers(session)) <= 1:
        raise TeacherScopeError("至少保留一位教师")
    was_active = _active_teacher_id(session) == teacher_id
    session.delete(teacher)
    session.flush()
    if was_active:
        remaining = list_teachers(session)
        if remaining:
            _set_active_teacher_id(session, remaining[0].id)


def update_presets(session, *, teacher_id: int, presets: Any,
                   term_id: int | None = None) -> TeacherProfile:
    """覆盖当前教师的补录预设；未知类别、越界分值直接拒绝而不是静默忽略。"""
    teacher = session.get(TeacherProfile, teacher_id)
    if teacher is None:
        raise TeacherScopeError("教师不存在")
    if not isinstance(presets, dict):
        raise TeacherScopeError("presets 必须是对象")
    allowed = set(_ordered_manual_types(growth_events.read_rule_version(session, term_id=term_id)))
    cleaned: dict[str, Any] = {}
    for event_type, entry in presets.items():
        key = str(event_type)
        if key not in allowed:
            raise TeacherScopeError(f"不支持的手工补录类别：{key}")
        if not isinstance(entry, dict):
            raise TeacherScopeError("每个类别的设置必须是对象")
        record: dict[str, Any] = {}
        if "points" in entry:
            points = entry["points"]
            if isinstance(points, bool) or not isinstance(points, int) or not 1 <= points <= MAX_MANUAL_POINTS:
                raise TeacherScopeError(f"分值必须是 1 到 {MAX_MANUAL_POINTS} 之间的整数")
            record["points"] = points
        if "visible" in entry:
            if not isinstance(entry["visible"], bool):
                raise TeacherScopeError("visible 必须是布尔值")
            record["visible"] = entry["visible"]
        if record:
            cleaned[key] = record
    teacher.presets_json = cleaned
    session.flush()
    return teacher


def teacher_payload(session, teacher: TeacherProfile | None) -> dict[str, Any] | None:
    if teacher is None:
        return None
    return {"id": teacher.id, "name": teacher.name}


def presets_conflict(session, *, term_id: int | None = None) -> dict[str, Any]:
    """检测本机多套标准是否已经不一致，供前端显式提示「营养值不宜横向比较」。

    只有当两位以上教师对同一类别给出了不同分值时才为真；单纯存在多位教师但
    标准一致时不算冲突，避免无谓的告警噪音。
    """
    teachers = list_teachers(session)
    if len(teachers) < 2:
        return {"conflict": False, "teachers": len(teachers), "divergent_types": []}
    divergent: set[str] = set()
    baseline: dict[str, int] = {}
    for teacher in teachers:
        for preset in resolve_presets(session, teacher=teacher, term_id=term_id):
            key, points = preset["type"], preset["points"]
            if key not in baseline:
                baseline[key] = points
            elif baseline[key] != points:
                divergent.add(key)
    return {
        "conflict": bool(divergent),
        "teachers": len(teachers),
        "divergent_types": sorted(divergent),
    }
