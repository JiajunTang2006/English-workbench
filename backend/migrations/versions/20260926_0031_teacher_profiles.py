"""Per-teacher manual bonus presets.

单机软件没有账号体系，教师档案只承载「本机有哪几位老师」以及每位老师自己的
补录加分标准。预设不改变计分引擎，只决定快捷按钮与默认分值；历史事件仍按
发生当时的分值入账。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0031"
down_revision = "20260926_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("presets_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_teacher_profiles_name"),
    )
    # 既有库已经有历史补录记录，但没有教师档案；用默认档案承接这些记录，
    # 让「当前教师」在升级后立刻有确定值，而不是等到第一次补录才创建。
    op.execute(sa.text("""
        INSERT INTO teacher_profiles (name, presets_json)
        SELECT '默认教师', '{}'
        WHERE NOT EXISTS (SELECT 1 FROM teacher_profiles)
    """))


def downgrade() -> None:
    op.drop_table("teacher_profiles")
