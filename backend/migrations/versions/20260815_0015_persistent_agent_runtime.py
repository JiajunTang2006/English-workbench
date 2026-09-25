"""U1-03/U2-02: Add harness_session_id, context_revision to agent_sessions;
add harness_message_id, runtime_generation to analysis_runs;
add analysis_run_events table for persistent run events."""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0015"
down_revision = "20260815_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # agent_sessions: harness_session_id (unique nullable), context_revision
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(
            sa.Column("harness_session_id", sa.String(100), nullable=True)
        )
        batch.add_column(
            sa.Column("context_revision", sa.Integer, nullable=False, server_default="0")
        )

    op.create_index(
        "ix_agent_sessions_harness_session_id",
        "agent_sessions",
        ["harness_session_id"],
        unique=True,
    )

    # analysis_runs: harness_message_id, runtime_generation
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(
            sa.Column("harness_message_id", sa.String(100), nullable=True)
        )
        batch.add_column(
            sa.Column("runtime_generation", sa.Integer, nullable=False, server_default="1")
        )

    # U2-02: analysis_run_events table
    op.create_table(
        "analysis_run_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("source", sa.String(20), nullable=False, server_default="gateway"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_run_seq"),
    )
    op.create_index(
        "ix_analysis_run_events_run_id",
        "analysis_run_events",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_run_events_run_id", table_name="analysis_run_events")
    op.drop_table("analysis_run_events")

    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_column("runtime_generation")
        batch.drop_column("harness_message_id")

    op.drop_index("ix_agent_sessions_harness_session_id", table_name="agent_sessions")

    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_column("context_revision")
        batch.drop_column("harness_session_id")
