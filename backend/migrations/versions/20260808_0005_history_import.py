"""考试分层线、考试时班级快照与数据兼容字段。"""

from alembic import op
import sqlalchemy as sa


revision = "20260808_0005"
down_revision = "20260807_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exams", sa.Column("exam_kind", sa.String(30), nullable=False, server_default="regular"))
    op.add_column("exams", sa.Column("tier_a_cutoff", sa.Float(), nullable=True))
    op.add_column("exams", sa.Column("tier_b_cutoff", sa.Float(), nullable=True))
    op.add_column("exams", sa.Column("tier_c_cutoff", sa.Float(), nullable=True))
    op.add_column("exam_scores", sa.Column("class_id_at_exam", sa.Integer(), nullable=True))
    op.create_index("ix_exam_scores_class_id_at_exam", "exam_scores", ["class_id_at_exam"])
    # 历史成绩先按导入时学生所在班级回填；之后的写入由服务层固定快照。
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE exam_scores SET class_id_at_exam = "
        "(SELECT class_id FROM students WHERE students.id = exam_scores.student_id) "
        "WHERE class_id_at_exam IS NULL"
    ))


def downgrade() -> None:
    op.drop_index("ix_exam_scores_class_id_at_exam", table_name="exam_scores")
    op.drop_column("exam_scores", "class_id_at_exam")
    op.drop_column("exams", "tier_c_cutoff")
    op.drop_column("exams", "tier_b_cutoff")
    op.drop_column("exams", "tier_a_cutoff")
    op.drop_column("exams", "exam_kind")
