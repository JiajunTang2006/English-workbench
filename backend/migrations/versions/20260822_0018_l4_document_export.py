"""L4 文档导出表（document_export_jobs / generated_artifacts）

方案 §11.3：统一 DocumentSpec v1 → 本地/远端 Provider → 产物登记。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0018"
down_revision: str = "20260822_0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "document_export_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("template_id", sa.String(length=30), nullable=False, server_default="report"),
        sa.Column("template_version", sa.String(length=20), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("input_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("remote_job_id", sa.String(length=120), nullable=True),
        sa.Column("output_artifact_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["output_artifact_id"], ["generated_artifacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_export_jobs_run_id", "document_export_jobs", ["run_id"])
    op.create_index("ix_document_export_jobs_session_id", "document_export_jobs", ["session_id"])
    op.create_index("ix_document_export_jobs_status", "document_export_jobs", ["status"])
    op.create_index("ix_document_export_jobs_output_artifact_id", "document_export_jobs", ["output_artifact_id"])

    op.create_table(
        "generated_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="document"),
        sa.Column("storage_name", sa.String(length=160), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contains_personal_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generated_artifacts_sha256", "generated_artifacts", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_generated_artifacts_sha256", table_name="generated_artifacts")
    op.drop_table("generated_artifacts")
    op.drop_index("ix_document_export_jobs_output_artifact_id", table_name="document_export_jobs")
    op.drop_index("ix_document_export_jobs_status", table_name="document_export_jobs")
    op.drop_index("ix_document_export_jobs_session_id", table_name="document_export_jobs")
    op.drop_index("ix_document_export_jobs_run_id", table_name="document_export_jobs")
    op.drop_table("document_export_jobs")
