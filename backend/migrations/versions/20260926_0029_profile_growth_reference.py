"""Store the growth-fact fingerprint a student profile was built from.

方案 §6.4：事实更正后相关画像要能标记为「依据已更新」。画像只保存它引用过的
成长源修订号与证据入口，真正的事实仍在成长事件账本里。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0029"
down_revision = "20260925_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "student_profiles",
        sa.Column("growth_reference_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("student_profiles", "growth_reference_json")
