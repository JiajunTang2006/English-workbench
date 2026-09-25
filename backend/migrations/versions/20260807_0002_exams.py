"""考试、题型和学生成绩。"""

from alembic import op
import sqlalchemy as sa


revision = "20260807_0002"
down_revision = "20260807_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(100)),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("exam_date", sa.Date()),
        sa.Column("full_score", sa.Float(), nullable=False),
        sa.Column("exam_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_key", name="uq_exams_source_key"),
    )
    op.create_table(
        "score_dimensions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("exam_id", "code", name="uq_score_dimensions_exam_code"),
        sa.UniqueConstraint("exam_id", "name", name="uq_score_dimensions_exam_name"),
    )
    op.create_index("ix_score_dimensions_exam_id", "score_dimensions", ["exam_id"])
    op.create_table(
        "exam_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("total_score", sa.Float()),
        sa.Column("attendance_status", sa.String(20), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exam_id", "student_id", name="uq_exam_scores_exam_student"),
    )
    op.create_index("ix_exam_scores_exam_id", "exam_scores", ["exam_id"])
    op.create_index("ix_exam_scores_student_id", "exam_scores", ["student_id"])
    op.create_table(
        "exam_dimension_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_score_id", sa.Integer(), sa.ForeignKey("exam_scores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension_id", sa.Integer(), sa.ForeignKey("score_dimensions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.UniqueConstraint("exam_score_id", "dimension_id", name="uq_exam_dimension_scores_pair"),
    )
    op.create_index("ix_exam_dimension_scores_exam_score_id", "exam_dimension_scores", ["exam_score_id"])
    op.create_index("ix_exam_dimension_scores_dimension_id", "exam_dimension_scores", ["dimension_id"])


def downgrade() -> None:
    op.drop_table("exam_dimension_scores")
    op.drop_table("exam_scores")
    op.drop_table("score_dimensions")
    op.drop_table("exams")
