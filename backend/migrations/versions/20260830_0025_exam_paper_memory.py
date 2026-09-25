"""Add exam paper memories (AI paper-understanding memory, teacher confirmed)."""

from alembic import op
import sqlalchemy as sa


revision = "20260830_0025"
down_revision = "20260829_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exam_paper_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_version_id", sa.Integer(), sa.ForeignKey("exam_paper_versions.id", ondelete="SET NULL")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="ai"),
        sa.Column("generation_model", sa.String(100)),
        sa.Column("knowledge_gaps_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("exam_id", "version", name="uq_paper_memory_exam_version"),
    )
    op.create_index("ix_exam_paper_memories_exam_id", "exam_paper_memories", ["exam_id"])
    op.create_index("ix_exam_paper_memories_paper_version_id", "exam_paper_memories", ["paper_version_id"])


def downgrade() -> None:
    op.drop_table("exam_paper_memories")
