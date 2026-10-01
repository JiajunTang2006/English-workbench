"""运行内身份词典 (B2-03)

每次分析运行建立本地身份词典，至少包含：学生姓名、学号、联系电话、
学校名称（可配置）与教师姓名等受保护文本。
本地保留原文；发送给 Provider 的只能是匿名副本（由 PrivacyMapper 完成）。

词典构建为只读查询，不修改任何正式数据。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class RuntimeIdentity:
    """运行内身份事实（本地保留，不外发）。"""
    student_ids: list[int] = field(default_factory=list)
    student_names: list[str] = field(default_factory=list)
    # 显式保留学生 ID 与姓名的绑定，避免依赖两个平行列表的索引关系。
    student_records: list[tuple[int, str]] = field(default_factory=list)
    student_nos: list[str] = field(default_factory=list)
    student_number_records: list[tuple[int, str]] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    school_names: list[str] = field(default_factory=list)
    protected_terms: list[str] = field(default_factory=list)


def _settings_value(db: Session, key: str) -> str | None:
    """从 AppSetting 读取配置值（缺失返回 None）。"""
    try:
        from ...models.entities import AppSetting
        row = db.get(AppSetting, key)
        if row is None:
            return None
        value = row.value_json
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value.get("value") or value.get("name")
        return None
    except Exception:
        return None


def build_run_identity_dictionary(
    db: Session,
    *,
    term_id: int | None = None,
    class_id: int | None = None,
    student_id: int | None = None,
) -> RuntimeIdentity:
    """从数据库构建当前作用域的身份词典。

    查询范围：term 下所有在读学生（student_id 单点或 class_id 班级，
    无班级时取学期全部学生）。同时读取教师姓名作为受保护文本。
    """
    identity = RuntimeIdentity()

    try:
        from ...models.entities import AppSetting, Student, Enrollment

        # 学生范围
        query = select(Student.id, Student.name, Student.student_no, Student.parent_phone)
        if student_id is not None:
            query = query.where(Student.id == student_id).limit(50)
        elif class_id is not None:
            query = query.join(Enrollment, Enrollment.student_id == Student.id).where(
                Enrollment.class_id == class_id,
                Enrollment.status == "active",
            )
            if term_id is not None:
                query = query.where(Enrollment.term_id == term_id)
        elif term_id is not None:
            query = query.join(Enrollment, Enrollment.student_id == Student.id).where(
                Enrollment.term_id == term_id,
                Enrollment.status == "active",
            )
        else:
            query = query.limit(500)

        rows = db.execute(query).all()

        # 某些历史同步会先创建一个同名班级，导致运行记录里的 class_id
        # 与学生 Enrollment 使用的 class_id 不一致。此时不能让本地身份
        # 词典变成空表，否则教师端会看到 student_01 这类占位符无法还原。
        # 仅在指定班级查不到任何有效关联时回退到同学期名单；所有值仍只
        # 留在本地映射中，外发前依旧会被匿名化。
        if not rows and class_id is not None and term_id is not None:
            fallback_query = (
                select(Student.id, Student.name, Student.student_no, Student.parent_phone)
                .join(Enrollment, Enrollment.student_id == Student.id)
                .where(
                    Enrollment.term_id == term_id,
                    Enrollment.status == "active",
                )
            )
            rows = db.execute(fallback_query).all()
            if rows:
                logger.info(
                    "班级 %s 未找到有效学生关联，身份词典回退到学期名单（%d 人）",
                    class_id,
                    len(rows),
                )

        # B3-04：按学生 ID 稳定排序，保证同一 scope 内跨 run 的匿名编号顺序一致
        # （同 session/context revision 内匿名编号保持稳定，不因查询顺序抖动）
        rows = sorted(rows, key=lambda r: r[0] if r[0] is not None else 0)
        for sid, name, student_no, phone in rows:
            if sid is not None:
                identity.student_ids.append(sid)
            if name:
                identity.student_names.append(name)
                if sid is not None:
                    identity.student_records.append((sid, name))
            if student_no:
                identity.student_nos.append(student_no)
                identity.student_number_records.append((sid, student_no))
            if phone:
                identity.phones.append(phone)
    except Exception:
        logger.warning("构建学生身份词典失败（B2-03）", exc_info=True)

    # 教师姓名等受保护文本
    teacher_name = _settings_value(db, "teacher_name")
    if teacher_name:
        identity.protected_terms.append(teacher_name)

    # 学校名称（可配置；支持 school_name / schoolName / school 键）
    for key in ("school_name", "schoolName", "school"):
        school_name = _settings_value(db, key)
        if school_name:
            identity.school_names.append(school_name)
            break

    return identity


def register_identity_into_mapper(mapper: Any, identity: RuntimeIdentity) -> None:
    """把运行内身份词典注册进 PrivacyMapper。

    - student_id：匿名编号映射；
    - 姓名：文本替换用匿名编号（尽可能绑定到学生编号）；
    - 学号/电话：正则脱敏（学号已在 sanitize_text 覆盖）；
    - 学校/受保护文本：注册标记。
    """
    # 先注册 id（生成稳定匿名编号），再为姓名绑定同一编号
    for sid in identity.student_ids:
        mapper.register_student(sid)

    # 使用显式记录关联姓名与 ID；旧字段仍保留用于兼容外部调用。
    registered_names: set[str] = set()
    for sid, name in identity.student_records:
        mapper.register_name(name, student_id=sid)
        registered_names.add(name)
    for name in identity.student_names:
        if name not in registered_names:
            mapper.register_name(name)

    mapper.register_school_names(identity.school_names)
    for sid, number in identity.student_number_records:
        mapper._student_number_refs[number] = mapper.to_anonymous(sid)
    # B3-04：电话与学号一并进入受保护文本（正则兜底 + 词典精确匹配双保险）
    mapper.register_protected_terms(
        identity.protected_terms + identity.student_nos + identity.phones
    )


def build_display_mapper(
    db: Session,
    *,
    term_id: int | None = None,
    class_id: int | None = None,
    student_id: int | None = None,
):
    """Build the local-only mapper used to rehydrate teacher-facing output.

    The model/bridge must continue to receive anonymised values.  This helper is
    deliberately separate from that outbound path and reconstructs the same
    deterministic scope mapping when a result is saved or loaded later.
    """
    from ...agent.privacy import PrivacyMapper

    mapper = PrivacyMapper(allow_student_names=True)
    identity = build_run_identity_dictionary(
        db, term_id=term_id, class_id=class_id, student_id=student_id,
    )
    register_identity_into_mapper(mapper, identity)
    return mapper
