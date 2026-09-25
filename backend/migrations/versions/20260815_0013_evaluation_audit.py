"""Add evaluation_audit_logs table and updated_at column to student_evaluations."""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0013"
down_revision = "20260815_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create evaluation_audit_logs table
    op.create_table(
        "evaluation_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "evaluation_id",
            sa.Integer(),
            sa.ForeignKey("student_evaluations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False, server_default="teacher"),
        sa.Column("detail_json", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Add updated_at column to student_evaluations
    with op.batch_alter_table("student_evaluations") as batch:
        batch.add_column(
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("student_evaluations") as batch:
        batch.drop_column("updated_at")

    op.drop_table("evaluation_audit_logs")
