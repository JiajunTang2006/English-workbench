"""Add the growth-tree event ledger, rule versions, awards and snapshots."""

from alembic import op
import sqlalchemy as sa


revision = "20260925_0028"
down_revision = "20260901_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "growth_rule_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("timezone", sa.String(60), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("stage_thresholds_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("event_rules_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("daily_total_cap", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("weekly_total_cap", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_growth_rule_versions_code"),
    )

    op.create_table(
        "growth_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id_at_event", sa.Integer(), sa.ForeignKey("classes.id", ondelete="SET NULL")),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_id", sa.String(150)),
        sa.Column("source_revision", sa.String(150)),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("actor", sa.String(40), nullable=False, server_default="teacher"),
        sa.Column("reverses_event_id", sa.Integer(), sa.ForeignKey("growth_events.id", ondelete="SET NULL")),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_growth_events_idempotency"),
    )
    op.create_index("ix_growth_events_student_id", "growth_events", ["student_id"])
    op.create_index("ix_growth_events_term_id", "growth_events", ["term_id"])
    op.create_index("ix_growth_events_class_id_at_event", "growth_events", ["class_id_at_event"])
    op.create_index("ix_growth_events_source_type", "growth_events", ["source_type"])
    op.create_index("ix_growth_events_source_id", "growth_events", ["source_id"])
    op.create_index("ix_growth_events_event_type", "growth_events", ["event_type"])
    op.create_index("ix_growth_events_occurred_at", "growth_events", ["occurred_at"])
    op.create_index("ix_growth_events_business_date", "growth_events", ["business_date"])
    op.create_index("ix_growth_events_reverses_event_id", "growth_events", ["reverses_event_id"])

    op.create_table(
        "growth_awards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("growth_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("week_key", sa.String(12), nullable=False),
        sa.Column("proposed_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cap_reason", sa.String(40), nullable=False, server_default="none"),
        sa.Column("calculation_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "calculation_revision", name="uq_growth_awards_event_revision"),
    )
    op.create_index("ix_growth_awards_event_id", "growth_awards", ["event_id"])
    op.create_index("ix_growth_awards_student_id", "growth_awards", ["student_id"])
    op.create_index("ix_growth_awards_term_id", "growth_awards", ["term_id"])
    op.create_index("ix_growth_awards_business_date", "growth_awards", ["business_date"])
    op.create_index("ix_growth_awards_week_key", "growth_awards", ["week_key"])

    op.create_table(
        "student_growth_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("term_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("legacy_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage_name", sa.String(30), nullable=False, server_default="种子"),
        sa.Column("week_key", sa.String(12), nullable=False, server_default=""),
        sa.Column("week_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("dimensions_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_revision", sa.String(64), nullable=False, server_default=""),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("student_id", "term_id", name="uq_growth_snapshots_student_term"),
    )
    op.create_index("ix_student_growth_snapshots_student_id", "student_growth_snapshots", ["student_id"])
    op.create_index("ix_student_growth_snapshots_term_id", "student_growth_snapshots", ["term_id"])


def downgrade() -> None:
    op.drop_table("student_growth_snapshots")
    op.drop_table("growth_awards")
    op.drop_table("growth_events")
    op.drop_table("growth_rule_versions")
