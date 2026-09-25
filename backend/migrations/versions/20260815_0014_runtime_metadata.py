"""Add runtime_kind, runtime_version, harness_session_id to analysis_runs."""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0014"
down_revision = "20260815_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(
            sa.Column("runtime_kind", sa.String(20), nullable=False, server_default="legacy")
        )
        batch.add_column(
            sa.Column("runtime_version", sa.String(50), nullable=True)
        )
        batch.add_column(
            sa.Column("harness_session_id", sa.String(100), nullable=True)
        )

    # Index for harness_session_id lookups
    op.create_index(
        "ix_analysis_runs_harness_session_id",
        "analysis_runs",
        ["harness_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_runs_harness_session_id", table_name="analysis_runs")

    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_column("harness_session_id")
        batch.drop_column("runtime_version")
        batch.drop_column("runtime_kind")
