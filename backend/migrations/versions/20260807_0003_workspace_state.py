"""旧界面完整工作台状态兼容表。"""

from alembic import op
import sqlalchemy as sa


revision = "20260807_0003"
down_revision = "20260807_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_states",
        sa.Column("name", sa.String(50), primary_key=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("workspace_states")
