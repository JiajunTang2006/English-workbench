"""Add controlled multi-agent analysis groups."""

from alembic import op
import sqlalchemy as sa


revision = "20260831_0026"
down_revision = "20260830_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_type", sa.String(40), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", sa.Integer()),
        sa.Column("exam_id", sa.Integer()),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("requested_student_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("estimated_cost_yuan", sa.Float()),
        sa.Column("actual_cost_yuan", sa.Float()),
        sa.Column("scope_snapshot_id", sa.String(100), nullable=False),
        sa.Column("scope_snapshot_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_by", sa.String(100), nullable=False, server_default="teacher"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_analysis_groups_term_id", "analysis_groups", ["term_id"])
    op.create_index("ix_analysis_groups_class_id", "analysis_groups", ["class_id"])
    op.create_index("ix_analysis_groups_exam_id", "analysis_groups", ["exam_id"])
    op.create_index("ix_analysis_groups_scope_snapshot_id", "analysis_groups", ["scope_snapshot_id"])

    op.create_table(
        "analysis_group_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("analysis_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_role", sa.String(40), nullable=False, server_default="student_worker"),
        sa.Column("shard_index", sa.Integer(), nullable=False),
        sa.Column("depends_on_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("student_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("group_id", "shard_index", name="uq_analysis_group_task_shard"),
    )
    op.create_index("ix_analysis_group_tasks_group_id", "analysis_group_tasks", ["group_id"])
    op.create_index("ix_analysis_group_tasks_analysis_run_id", "analysis_group_tasks", ["analysis_run_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_group_tasks_analysis_run_id", table_name="analysis_group_tasks")
    op.drop_index("ix_analysis_group_tasks_group_id", table_name="analysis_group_tasks")
    op.drop_table("analysis_group_tasks")
    op.drop_index("ix_analysis_groups_scope_snapshot_id", table_name="analysis_groups")
    op.drop_index("ix_analysis_groups_exam_id", table_name="analysis_groups")
    op.drop_index("ix_analysis_groups_class_id", table_name="analysis_groups")
    op.drop_index("ix_analysis_groups_term_id", table_name="analysis_groups")
    op.drop_table("analysis_groups")
