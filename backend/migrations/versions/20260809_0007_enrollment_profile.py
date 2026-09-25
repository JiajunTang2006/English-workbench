"""Move term-specific student profile fields onto enrollments."""

from alembic import op
import sqlalchemy as sa


revision = "20260809_0007"
down_revision = "20260809_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("enrollments") as batch:
        batch.add_column(sa.Column("entrance_english", sa.Float()))
        batch.add_column(sa.Column("target_score", sa.Float()))
        batch.add_column(sa.Column("weak_tags", sa.Text()))
        batch.add_column(sa.Column("parent_phone", sa.String(30)))
        batch.add_column(sa.Column("seat", sa.String(30)))
    connection = op.get_bind()
    for column in ("entrance_english", "target_score", "weak_tags", "parent_phone", "seat"):
        connection.execute(sa.text(
            f"UPDATE enrollments SET {column} = "
            f"(SELECT students.{column} FROM students WHERE students.id = enrollments.student_id)"
        ))


def downgrade() -> None:
    with op.batch_alter_table("enrollments") as batch:
        batch.drop_column("seat")
        batch.drop_column("parent_phone")
        batch.drop_column("weak_tags")
        batch.drop_column("target_score")
        batch.drop_column("entrance_english")
