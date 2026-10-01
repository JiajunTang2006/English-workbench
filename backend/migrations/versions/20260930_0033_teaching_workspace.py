"""Persistent teaching workspaces and feedback, additive to existing data."""
from alembic import op
import sqlalchemy as sa

revision = "20260930_0033"
down_revision = "20260927_0032"
branch_labels = None
depends_on = None

def upgrade():
    op.execute('CREATE TABLE teaching_tasks (\n\tid INTEGER NOT NULL, \n\tterm_id INTEGER NOT NULL, \n\tsubject_key VARCHAR(40) NOT NULL, \n\tclass_id INTEGER, \n\texam_id INTEGER, \n\ttitle VARCHAR(200) NOT NULL, \n\tgoal TEXT NOT NULL, \n\tconstraints_json JSON NOT NULL, \n\tphase VARCHAR(30) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(term_id) REFERENCES terms (id) ON DELETE CASCADE, \n\tFOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE SET NULL, \n\tFOREIGN KEY(exam_id) REFERENCES exams (id) ON DELETE SET NULL\n)')
    op.execute('CREATE INDEX ix_teaching_tasks_term_id ON teaching_tasks (term_id)')
    op.execute('CREATE TABLE teaching_artifacts (\n\tid INTEGER NOT NULL, \n\ttask_id INTEGER NOT NULL, \n\tkind VARCHAR(40) NOT NULL, \n\ttitle VARCHAR(200) NOT NULL, \n\tbody TEXT NOT NULL, \n\titems_json JSON NOT NULL, \n\trevision INTEGER NOT NULL, \n\tsource_run_id INTEGER, \n\tsource_section INTEGER, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_task_source_section UNIQUE (task_id, source_run_id, source_section), \n\tFOREIGN KEY(task_id) REFERENCES teaching_tasks (id) ON DELETE CASCADE, \n\tFOREIGN KEY(source_run_id) REFERENCES analysis_runs (id) ON DELETE SET NULL\n)')
    op.execute('CREATE INDEX ix_teaching_artifacts_task_id ON teaching_artifacts (task_id)')
    op.execute('CREATE TABLE teaching_artifact_revisions (\n\tid INTEGER NOT NULL, \n\tartifact_id INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcontent_json JSON NOT NULL, \n\torigin VARCHAR(30) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_artifact_revision UNIQUE (artifact_id, revision), \n\tFOREIGN KEY(artifact_id) REFERENCES teaching_artifacts (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_teaching_artifact_revisions_artifact_id ON teaching_artifact_revisions (artifact_id)')
    op.execute('CREATE TABLE teaching_feedback (\n\tid INTEGER NOT NULL, \n\ttask_id INTEGER NOT NULL, \n\tkind VARCHAR(30) NOT NULL, \n\tnote TEXT NOT NULL, \n\tobservations_json JSON NOT NULL, \n\tcontext_json JSON NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(task_id) REFERENCES teaching_tasks (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_teaching_feedback_task_id ON teaching_feedback (task_id)')
    # SQLite ADD COLUMN supports a nullable reference without rebuilding old rows.
    op.add_column("agent_sessions", sa.Column("teaching_task_id", sa.Integer(), nullable=True))
    op.create_index("ix_agent_sessions_teaching_task_id", "agent_sessions", ["teaching_task_id"])
    with op.batch_alter_table("agent_sessions") as batch:
        batch.create_foreign_key("fk_agent_session_teaching_task", "teaching_tasks", ["teaching_task_id"], ["id"], ondelete="SET NULL")

def downgrade():
    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_constraint("fk_agent_session_teaching_task", type_="foreignkey")
        batch.drop_index("ix_agent_sessions_teaching_task_id")
        batch.drop_column("teaching_task_id")
    op.drop_table("teaching_feedback")
    op.drop_table("teaching_artifact_revisions")
    op.drop_table("teaching_artifacts")
    op.drop_table("teaching_tasks")
