"""学生年级排名与班级考试年级排名。"""

from alembic import op
import sqlalchemy as sa


revision = "20260807_0004"
down_revision = "20260807_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exam_scores", sa.Column("grade_rank", sa.Integer(), nullable=True))
    op.create_table(
        "exam_class_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grade_rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exam_id", "class_id", name="uq_exam_class_metrics_pair"),
    )
    op.create_index("ix_exam_class_metrics_exam_id", "exam_class_metrics", ["exam_id"])
    op.create_index("ix_exam_class_metrics_class_id", "exam_class_metrics", ["class_id"])


def downgrade() -> None:
    op.drop_index("ix_exam_class_metrics_class_id", table_name="exam_class_metrics")
    op.drop_index("ix_exam_class_metrics_exam_id", table_name="exam_class_metrics")
    op.drop_table("exam_class_metrics")
    op.drop_column("exam_scores", "grade_rank")
