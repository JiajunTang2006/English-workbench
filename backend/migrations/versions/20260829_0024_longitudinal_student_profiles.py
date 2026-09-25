"""Add cross-term longitudinal student profiles."""

from alembic import op
import sqlalchemy as sa


revision = "20260829_0024"
down_revision = "20260827_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_longitudinal_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_source_term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="SET NULL")),
        sa.Column("last_analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")),
        sa.Column("needs_compression", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(100), nullable=False, server_default="teacher"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("student_id", name="uq_student_longitudinal_profiles_student"),
    )
    op.create_index("ix_student_longitudinal_profiles_student_id", "student_longitudinal_profiles", ["student_id"])
    op.create_index("ix_student_longitudinal_profiles_last_source_term_id", "student_longitudinal_profiles", ["last_source_term_id"])
    op.create_index("ix_student_longitudinal_profiles_last_analysis_run_id", "student_longitudinal_profiles", ["last_analysis_run_id"])


def downgrade() -> None:
    op.drop_index("ix_student_longitudinal_profiles_last_analysis_run_id", table_name="student_longitudinal_profiles")
    op.drop_index("ix_student_longitudinal_profiles_last_source_term_id", table_name="student_longitudinal_profiles")
    op.drop_index("ix_student_longitudinal_profiles_student_id", table_name="student_longitudinal_profiles")
    op.drop_table("student_longitudinal_profiles")
