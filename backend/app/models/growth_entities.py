"""成长树数据模型（方案 §5.1）。

设计原则：
- 事件账本记录「发生了什么」，奖励明细记录「为什么给这些点」，两者分离。
- 规则版本不可变：教师调整参数时产生新版本，既有记录仍可按旧规则复算。
- 快照是可重建缓存，不是第二套事实：所有数值都能由事件 + 规则重算。
- 来源修订有唯一约束，重试同一请求不得重复计分。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .entities import utcnow


class GrowthRuleVersion(Base):
    """不可变的成长规则版本。

    阶段阈值、封顶、时区、事件参数都固化在版本里。教师修改参数时新增一行，
    不在原版本上原地修改，保证历史奖励可回放。
    """

    __tablename__ = "growth_rule_versions"
    __table_args__ = (UniqueConstraint("code", name="uq_growth_rule_versions_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # active / retired
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Shanghai", nullable=False)
    stage_thresholds_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    event_rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    daily_total_cap: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    weekly_total_cap: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GrowthTermRule(Base):
    """A term is bound to one immutable rule code from its first growth write."""

    __tablename__ = "growth_term_rules"

    term_id: Mapped[int] = mapped_column(
        ForeignKey("terms.id", ondelete="CASCADE"), primary_key=True)
    rule_code: Mapped[str] = mapped_column(
        ForeignKey("growth_rule_versions.code"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GrowthEvent(Base):
    """学习事件账本。

    稳定 ID + 幂等键：同一次活动的自动记录、补录、重传必须合并为一条事实，
    重试同一请求不得新增积分。更正不删除原记录，而是追加一条引用原事件的
    撤销事件。
    """

    __tablename__ = "growth_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_growth_events_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    term_id: Mapped[int] = mapped_column(
        ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id_at_event: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id", ondelete="SET NULL"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # exam / dictation / homework / teacher / correction / migration
    source_id: Mapped[str | None] = mapped_column(String(150), index=True)
    source_revision: Mapped[str | None] = mapped_column(String(150))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(40), default="teacher", nullable=False)
    reverses_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("growth_events.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GrowthAward(Base):
    """事件对应的奖励明细：原本可得、实际入账、封顶原因。

    与事件一对一（按计算版本），由规则服务重建。原始学习证据不因封顶而丢失。
    """

    __tablename__ = "growth_awards"
    __table_args__ = (
        UniqueConstraint("event_id", "calculation_revision",
                         name="uq_growth_awards_event_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("growth_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    term_id: Mapped[int] = mapped_column(
        ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    week_key: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    proposed_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    applied_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cap_reason: Mapped[str] = mapped_column(String(40), default="none", nullable=False)
    calculation_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudentGrowthSnapshot(Base):
    """学生学期成长快照：可重建缓存，不是第二套事实。

    数值字段由后端确定性输出；模型只能读取，不能修改积分或置信状态。
    """

    __tablename__ = "student_growth_snapshots"
    __table_args__ = (
        UniqueConstraint("student_id", "term_id", name="uq_growth_snapshots_student_term"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    term_id: Mapped[int] = mapped_column(
        ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    term_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    legacy_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_name: Mapped[str] = mapped_column(String(30), default="种子", nullable=False)
    week_key: Mapped[str] = mapped_column(String(12), default="", nullable=False)
    week_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dimensions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_revision: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
