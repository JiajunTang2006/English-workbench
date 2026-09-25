"""Track source sync and teacher overrides on imported scores."""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0020"
down_revision = "20260824_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("exam_scores") as batch:
        batch.add_column(sa.Column("source_sync_run_id", sa.Integer(), sa.ForeignKey("school_sync_runs.id", name="fk_exam_scores_source_sync_run", ondelete="SET NULL")))
        batch.add_column(sa.Column("teacher_override", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("override_note", sa.String(500)))
        batch.create_index("ix_exam_scores_source_sync_run_id", ["source_sync_run_id"])
    with op.batch_alter_table("exam_paper_versions") as batch:
        batch.add_column(sa.Column("source_sync_run_id", sa.Integer(), sa.ForeignKey("school_sync_runs.id", name="fk_exam_paper_versions_source_sync_run", ondelete="SET NULL")))
        batch.create_index("ix_exam_paper_versions_source_sync_run_id", ["source_sync_run_id"])
    with op.batch_alter_table("student_item_results") as batch:
        batch.add_column(sa.Column("source_sync_run_id", sa.Integer(), sa.ForeignKey("school_sync_runs.id", name="fk_student_item_results_source_sync_run", ondelete="SET NULL")))
        batch.add_column(sa.Column("source_record_id", sa.String(150)))
        batch.add_column(sa.Column("teacher_override", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("override_note", sa.String(500)))
        batch.create_index("ix_student_item_results_source_sync_run_id", ["source_sync_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("student_item_results") as batch:
        batch.drop_index("ix_student_item_results_source_sync_run_id")
        batch.drop_column("override_note")
        batch.drop_column("teacher_override")
        batch.drop_column("source_record_id")
        batch.drop_column("source_sync_run_id")
    with op.batch_alter_table("exam_paper_versions") as batch:
        batch.drop_index("ix_exam_paper_versions_source_sync_run_id")
        batch.drop_column("source_sync_run_id")
    with op.batch_alter_table("exam_scores") as batch:
        batch.drop_index("ix_exam_scores_source_sync_run_id")
        batch.drop_column("override_note")
        batch.drop_column("teacher_override")
        batch.drop_column("source_sync_run_id")
