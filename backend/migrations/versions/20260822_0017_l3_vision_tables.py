"""L3: 新增 attachment_derivatives 与 attachment_analysis_results 表（视觉闭环）。

方案 §10.3：页级派生物与视觉/OCR 分析结果（含状态机与审计字段）。
"""

from alembic import op
import sqlalchemy as sa

revision = "20260822_0017"
down_revision = "20260817_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachment_derivatives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("storage_name", sa.String(length=160), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachment_derivatives_attachment_id",
                    "attachment_derivatives", ["attachment_id"])
    op.create_index("ix_attachment_derivatives_kind",
                    "attachment_derivatives", ["kind"])
    op.create_index("ix_attachment_derivatives_sha256",
                    "attachment_derivatives", ["sha256"])

    op.create_table(
        "attachment_analysis_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=80), nullable=True),
        sa.Column("result_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("teacher_correction_json", sa.JSON(), nullable=True),
        sa.Column("cost_yuan", sa.Float(), nullable=True),
        sa.Column("provider_request_id", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachment_analysis_results_attachment_id",
                    "attachment_analysis_results", ["attachment_id"])
    op.create_index("ix_attachment_analysis_results_result_type",
                    "attachment_analysis_results", ["result_type"])
    op.create_index("ix_attachment_analysis_results_status",
                    "attachment_analysis_results", ["status"])


def downgrade() -> None:
    op.drop_table("attachment_analysis_results")
    op.drop_table("attachment_derivatives")
