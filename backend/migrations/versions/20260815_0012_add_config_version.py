"""Add config_version column to analysis_runs for provider config auditing."""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0012"
down_revision = "20260815_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(
            sa.Column("config_version", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_column("config_version")
