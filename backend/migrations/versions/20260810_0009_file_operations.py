"""Persist filesystem work that follows database transactions."""

from alembic import op
import sqlalchemy as sa

revision = "20260810_0009"
down_revision = "20260809_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_file_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("source_path", sa.String(1000), nullable=False),
        sa.Column("target_path", sa.String(1000)),
        sa.Column("expected_size", sa.Integer()),
        sa.Column("expected_sha256", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pending_file_operations_status", "pending_file_operations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pending_file_operations_status", table_name="pending_file_operations")
    op.drop_table("pending_file_operations")
