"""Raise the default analysis-group concurrency to four workers."""

from alembic import op


revision = "20260901_0027"
down_revision = "20260831_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing groups keep their explicitly requested limit.  This only changes
    # the database default for groups created without a concurrency value.
    with op.batch_alter_table("analysis_groups") as batch:
        batch.alter_column("max_concurrency", server_default="4")


def downgrade() -> None:
    with op.batch_alter_table("analysis_groups") as batch:
        batch.alter_column("max_concurrency", server_default="2")
