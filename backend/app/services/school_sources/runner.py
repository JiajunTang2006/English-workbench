"""同步结果入库的共用尾部。

MONI 与自定义 MCP 数据源只是「怎么取数」不同；取到 ``SchoolSyncPayload`` 之后的
入库路径必须完全一致，否则两个来源会写出不同口径的数据。这里把这段共用逻辑
抽出来，MONI 也改走同一入口。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ...config import Settings
from ...models import AppSetting, Term
from ...schemas.school_sync import SchoolSyncPayload, StudentRosterPayload
from ..school_sync import apply_payload, apply_student_roster, rebuild_compat_exam_snapshots


def set_active_term(session: Session, term_id: int) -> None:
    """把导入的学期设为当前学期；切换前先准备学生档案继承。"""
    from ..student_profiles import prepare_student_profile_inheritance
    from ..terms import current_term_id

    try:
        previous_term_id = current_term_id(session)
    except Exception:
        previous_term_id = None
    if previous_term_id != term_id:
        prepare_student_profile_inheritance(session, previous_term_id, term_id)
    setting = session.get(AppSetting, "active_term_id")
    if setting is None:
        session.add(AppSetting(key="active_term_id", value_json=term_id))
    else:
        setting.value_json = term_id


def open_session(settings: Settings) -> sessionmaker[Session]:
    """确保库结构最新后返回会话工厂（迁移是幂等的）。"""
    from ...database import create_session_factory, run_migrations

    run_migrations(settings.database_url)
    return create_session_factory(settings.database_url)


def apply_payload_batch(
    settings: Settings,
    payloads: list[SchoolSyncPayload],
    *,
    term_code: str | None = None,
) -> list[dict[str, Any]]:
    """把一批考试 payload 写入库，并回填兼容快照。

    多个班级的 payload 分别提交后统一回填一次旧前端快照，确保成绩页同时覆盖
    所有班级，而不是只保留最后一批。
    """
    if not payloads:
        return []
    factory = open_session(settings)
    summaries: list[dict[str, Any]] = []
    with factory() as session:
        for payload in payloads:
            _, summary = apply_payload(session, payload)
            summaries.append(summary)
        key = term_code or payloads[0].term.code or payloads[0].term.external_id
        imported_term = session.scalar(select(Term).where(Term.code == key))
        if imported_term is not None:
            set_active_term(session, imported_term.id)
        rebuild_compat_exam_snapshots(session, key)
        session.commit()
    return summaries


def apply_roster_payload(settings: Settings, payload: StudentRosterPayload) -> dict[str, Any]:
    """写入名册，并把对应学期设为当前学期。"""
    factory = open_session(settings)
    with factory() as session:
        summary = apply_student_roster(session, payload)
        term_id = summary.get("term_id")
        if term_id is not None:
            set_active_term(session, int(term_id))
            session.commit()
    return summary
