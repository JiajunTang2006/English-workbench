"""Add teacher-confirmed student profile snapshots and revisions."""

from alembic import op
import sqlalchemy as sa


revision = "20260827_0023"
down_revision = "20260827_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.String(100), nullable=False, server_default="teacher"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("student_id", "term_id", name="uq_student_profiles_student_term"),
    )
    op.create_index("ix_student_profiles_student_id", "student_profiles", ["student_id"])
    op.create_index("ix_student_profiles_term_id", "student_profiles", ["term_id"])
    op.create_index("ix_student_profiles_last_analysis_run_id", "student_profiles", ["last_analysis_run_id"])

    op.create_table(
        "student_profile_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("student_profiles.id", ondelete="SET NULL")),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")),
        sa.Column("base_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proposed_patch_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_by", sa.String(100)),
        sa.Column("rejection_reason", sa.String(500)),
    )
    op.create_index("ix_student_profile_revisions_student_id", "student_profile_revisions", ["student_id"])
    op.create_index("ix_student_profile_revisions_term_id", "student_profile_revisions", ["term_id"])
    op.create_index("ix_student_profile_revisions_profile_id", "student_profile_revisions", ["profile_id"])
    op.create_index("ix_student_profile_revisions_analysis_run_id", "student_profile_revisions", ["analysis_run_id"])


def downgrade() -> None:
    op.drop_index("ix_student_profile_revisions_analysis_run_id", table_name="student_profile_revisions")
    op.drop_index("ix_student_profile_revisions_profile_id", table_name="student_profile_revisions")
    op.drop_index("ix_student_profile_revisions_term_id", table_name="student_profile_revisions")
    op.drop_index("ix_student_profile_revisions_student_id", table_name="student_profile_revisions")
    op.drop_table("student_profile_revisions")
    op.drop_index("ix_student_profiles_last_analysis_run_id", table_name="student_profiles")
    op.drop_index("ix_student_profiles_term_id", table_name="student_profiles")
    op.drop_index("ix_student_profiles_student_id", table_name="student_profiles")
    op.drop_table("student_profiles")
