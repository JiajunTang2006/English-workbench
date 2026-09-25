"""U4 fix: Add next_attempt_at to background_jobs for real exponential backoff."""

from alembic import op
import sqlalchemy as sa


revision = "20260817_0016"
down_revision = "20260815_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("background_jobs") as batch:
        batch.add_column(
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("background_jobs") as batch:
        batch.drop_column("next_attempt_at")
