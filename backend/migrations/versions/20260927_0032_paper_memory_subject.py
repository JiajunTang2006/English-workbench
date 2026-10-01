"""Store the subject of a confirmed exam-paper memory as structured data."""

from alembic import op
import sqlalchemy as sa


revision = "20260927_0032"
down_revision = "20260926_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All preexisting memories were produced in the original English workspace.
    with op.batch_alter_table("exam_paper_memories") as batch:
        batch.add_column(sa.Column("subject_key", sa.String(40), nullable=False,
                                   server_default="english"))
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(sa.Column("subject_key", sa.String(40), nullable=False,
                                   server_default="english"))
        batch.create_index("ix_agent_sessions_subject_key", ["subject_key"])
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("subject_key", sa.String(40), nullable=False,
                                   server_default="english"))
        batch.create_index("ix_analysis_runs_subject_key", ["subject_key"])
    with op.batch_alter_table("agent_messages") as batch:
        batch.add_column(sa.Column("teacher_material_edits_json", sa.JSON(), nullable=False,
                                   server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("agent_messages") as batch:
        batch.drop_column("teacher_material_edits_json")
    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_index("ix_analysis_runs_subject_key")
        batch.drop_column("subject_key")
    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_index("ix_agent_sessions_subject_key")
        batch.drop_column("subject_key")
    with op.batch_alter_table("exam_paper_memories") as batch:
        batch.drop_column("subject_key")
