"""Store MONI question metadata and per-item score facts."""

from alembic import op
import sqlalchemy as sa


revision = "20260825_0021"
down_revision = "20260824_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("exam_scores") as batch:
        batch.add_column(sa.Column("class_rank", sa.Integer()))
        batch.add_column(sa.Column("global_rank", sa.Integer()))
    with op.batch_alter_table("exam_questions") as batch:
        batch.add_column(sa.Column("sub_question_no", sa.String(20)))
        batch.add_column(sa.Column("difficulty_level", sa.String(50)))
        batch.add_column(sa.Column("cognitive_level", sa.String(50)))
        batch.add_column(sa.Column("knowledge_nodes_json", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("ability_nodes_json", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("pitfall_tags_json", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("teaching_blocks_json", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("student_item_results") as batch:
        batch.add_column(sa.Column("score_rate", sa.Float()))
        batch.add_column(sa.Column("correct", sa.Boolean()))
        batch.add_column(sa.Column("selected_option", sa.String(100)))
        batch.add_column(sa.Column("time_spent_ms", sa.Integer()))
        batch.add_column(sa.Column("modify_count", sa.Integer()))
        batch.add_column(sa.Column("hesitation_time_ms", sa.Integer()))
        batch.add_column(sa.Column("teaching_blocks_json", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("pitfall_tags_json", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("student_item_results") as batch:
        for name in ("pitfall_tags_json", "teaching_blocks_json", "hesitation_time_ms", "modify_count", "time_spent_ms", "selected_option", "correct", "score_rate"):
            batch.drop_column(name)
    with op.batch_alter_table("exam_questions") as batch:
        for name in ("teaching_blocks_json", "pitfall_tags_json", "ability_nodes_json", "knowledge_nodes_json", "cognitive_level", "difficulty_level", "sub_question_no"):
            batch.drop_column(name)
    with op.batch_alter_table("exam_scores") as batch:
        batch.drop_column("global_rank")
        batch.drop_column("class_rank")
