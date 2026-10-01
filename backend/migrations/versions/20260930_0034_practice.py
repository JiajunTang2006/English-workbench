"""Add targeted practices and version-bound attempts without replacing old records."""
from alembic import op
import sqlalchemy as sa

revision = "20260930_0034"
down_revision = "20260930_0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("teaching_tasks", sa.Column("target_type", sa.String(20), nullable=False, server_default="class"))
    op.add_column("teaching_tasks", sa.Column("student_ids_json", sa.JSON(), nullable=False, server_default="[]"))
    # Explicit table creation from the declared additive models, frozen column list.
    op.create_table("practice_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("teaching_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False), sa.Column("objective", sa.String(300), nullable=False),
        sa.Column("student_ids_json", sa.JSON(), nullable=False), sa.Column("level", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("practice_sets.id", ondelete="SET NULL")),
        sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")),
        sa.Column("checks_json", sa.JSON(), nullable=False), sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_practice_sets_task_id", "practice_sets", ["task_id"])
    op.create_table("practice_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("practice_id", sa.Integer(), sa.ForeignKey("practice_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False), sa.Column("objective", sa.String(300), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("practice_id", "position", name="uq_practice_question_position"))
    op.create_index("ix_practice_questions_practice_id", "practice_questions", ["practice_id"])
    op.create_table("practice_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("practice_id", sa.Integer(), sa.ForeignKey("practice_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("practice_questions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_key", sa.String(100), nullable=False), sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False), sa.Column("hint_level", sa.Integer(), nullable=False),
        sa.Column("answer_viewed", sa.Boolean(), nullable=False), sa.Column("new_question", sa.Boolean(), nullable=False),
        sa.Column("correct", sa.Boolean()), sa.Column("graded_by", sa.String(30), nullable=False),
        sa.Column("feedback_json", sa.JSON(), nullable=False), sa.Column("corrections_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("practice_id", "submission_key", name="uq_practice_submission"))
    for field in ("practice_id", "question_id", "student_id"):
        op.create_index("ix_practice_attempts_" + field, "practice_attempts", [field])


def downgrade():
    for table in ("practice_attempts", "practice_questions", "practice_sets"):
        op.drop_table(table)
    with op.batch_alter_table("teaching_tasks") as batch:
        batch.drop_column("student_ids_json")
        batch.drop_column("target_type")
