"""Add school data source and synchronization metadata."""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0019"
down_revision = "20260822_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "school_data_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="mock"),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_key", name="uq_school_data_sources_key"),
    )
    op.create_table(
        "external_entity_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("school_data_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(150), nullable=False),
        sa.Column("local_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "entity_type", "external_id", name="uq_external_entity_mapping"),
    )
    op.create_index("ix_external_entity_mappings_source_id", "external_entity_mappings", ["source_id"])
    op.create_table(
        "school_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("school_data_sources.id", ondelete="SET NULL")),
        sa.Column("snapshot_id", sa.String(150)),
        sa.Column("status", sa.String(30), nullable=False, server_default="preview"),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_school_sync_runs_source_id", "school_sync_runs", ["source_id"])
    op.create_index("ix_school_sync_runs_status", "school_sync_runs", ["status"])
    with op.batch_alter_table("exam_paper_versions") as batch:
        batch.add_column(sa.Column("external_revision", sa.String(150)))
        batch.add_column(sa.Column("structure_hash", sa.String(64)))
        batch.create_index("ix_exam_paper_versions_structure_hash", ["structure_hash"])
    with op.batch_alter_table("exam_questions") as batch:
        batch.add_column(sa.Column("external_id", sa.String(150)))
        batch.create_index("ix_exam_questions_external_id", ["external_id"])


def downgrade() -> None:
    with op.batch_alter_table("exam_questions") as batch:
        batch.drop_index("ix_exam_questions_external_id")
        batch.drop_column("external_id")
    with op.batch_alter_table("exam_paper_versions") as batch:
        batch.drop_index("ix_exam_paper_versions_structure_hash")
        batch.drop_column("structure_hash")
        batch.drop_column("external_revision")
    op.drop_index("ix_school_sync_runs_status", table_name="school_sync_runs")
    op.drop_index("ix_school_sync_runs_source_id", table_name="school_sync_runs")
    op.drop_table("school_sync_runs")
    op.drop_index("ix_external_entity_mappings_source_id", table_name="external_entity_mappings")
    op.drop_table("external_entity_mappings")
    op.drop_table("school_data_sources")
