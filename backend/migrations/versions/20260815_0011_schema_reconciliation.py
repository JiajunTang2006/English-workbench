"""Reconcile legacy migration metadata with the current ORM contract."""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0011"
down_revision = "20260815_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("exam_scores") as batch:
        batch.create_foreign_key(
            "fk_exam_scores_class_id_at_exam_classes",
            "classes",
            ["class_id_at_exam"],
            ["id"],
        )
    with op.batch_alter_table("students") as batch:
        batch.alter_column(
            "class_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("students") as batch:
        batch.alter_column(
            "class_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
    with op.batch_alter_table("exam_scores") as batch:
        batch.drop_constraint(
            "fk_exam_scores_class_id_at_exam_classes",
            type_="foreignkey",
        )
