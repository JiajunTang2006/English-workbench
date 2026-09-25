"""Bind each growth term to an immutable scoring rule version."""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0030"
down_revision = "20260926_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "growth_term_rules",
        sa.Column("term_id", sa.Integer(),
                  sa.ForeignKey("terms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("rule_code", sa.String(40),
                  sa.ForeignKey("growth_rule_versions.code"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    # Preserve the earliest persisted rule from existing growth terms.
    op.execute(sa.text("""
        INSERT INTO growth_term_rules (term_id, rule_code)
        SELECT snapshots.term_id, snapshots.rule_version
        FROM student_growth_snapshots AS snapshots
        WHERE snapshots.id = (
            SELECT MIN(earlier.id) FROM student_growth_snapshots AS earlier
            WHERE earlier.term_id = snapshots.term_id
        )
    """))
    op.execute(sa.text("""
        INSERT INTO growth_term_rules (term_id, rule_code)
        SELECT awards.term_id, awards.rule_version
        FROM growth_awards AS awards
        WHERE NOT EXISTS (
            SELECT 1 FROM growth_term_rules AS bound
            WHERE bound.term_id = awards.term_id
        )
        AND awards.id = (
            SELECT MIN(earlier.id) FROM growth_awards AS earlier
            WHERE earlier.term_id = awards.term_id
        )
    """))


def downgrade() -> None:
    op.drop_table("growth_term_rules")
