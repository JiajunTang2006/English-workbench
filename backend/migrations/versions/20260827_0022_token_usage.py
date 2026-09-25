"""Add cache/reasoning token observability fields."""

from alembic import op
import sqlalchemy as sa

revision = "20260827_0022"
down_revision = "20260825_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_usage_records") as batch:
        batch.add_column(sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("provider_prompt_tokens", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("llm_usage_records") as batch:
        batch.drop_column("provider_prompt_tokens")
        batch.drop_column("reasoning_tokens")
        batch.drop_column("cache_read_tokens")
