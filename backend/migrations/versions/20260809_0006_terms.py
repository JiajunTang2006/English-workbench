"""学期、学期班级、在班关系与学期工作区隔离。"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "20260809_0006"
down_revision = "20260808_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = datetime.now(timezone.utc)
    op.create_table(
        "terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("starts_on", sa.Date()),
        sa.Column("ends_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_terms_code"),
    )
    connection = op.get_bind()
    result = connection.execute(sa.text(
        "INSERT INTO terms (code, name, status, created_at, updated_at) "
        "VALUES (:code, :name, 'active', :now, :now)"
    ), {"code": "initial", "name": "初始学期", "now": now})
    term_id = result.lastrowid

    with op.batch_alter_table("classes") as batch:
        batch.add_column(sa.Column("term_id", sa.Integer(), nullable=True))
    connection.execute(sa.text("UPDATE classes SET term_id = :term_id"), {"term_id": term_id})
    with op.batch_alter_table("classes") as batch:
        batch.alter_column("term_id", nullable=False)
        batch.create_foreign_key("fk_classes_term_id", "terms", ["term_id"], ["id"], ondelete="RESTRICT")
        batch.drop_constraint("uq_classes_name", type_="unique")
        batch.create_unique_constraint("uq_classes_term_name", ["term_id", "name"])
        batch.create_index("ix_classes_term_id", ["term_id"])

    op.create_table(
        "enrollments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("joined_on", sa.Date()),
        sa.Column("left_on", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("term_id", "student_id", name="uq_enrollments_term_student"),
        sa.UniqueConstraint("term_id", "class_id", "student_id", name="uq_enrollments_term_class_student"),
    )
    for column in ("term_id", "class_id", "student_id"):
        op.create_index(f"ix_enrollments_{column}", "enrollments", [column])
    connection.execute(sa.text(
        "INSERT INTO enrollments "
        "(term_id, class_id, student_id, status, created_at, updated_at) "
        "SELECT :term_id, class_id, id, 'active', :now, :now FROM students WHERE class_id IS NOT NULL"
    ), {"term_id": term_id, "now": now})

    with op.batch_alter_table("exams") as batch:
        batch.add_column(sa.Column("term_id", sa.Integer(), nullable=True))
    connection.execute(sa.text("UPDATE exams SET term_id = :term_id"), {"term_id": term_id})
    with op.batch_alter_table("exams") as batch:
        batch.alter_column("term_id", nullable=False)
        batch.create_foreign_key("fk_exams_term_id", "terms", ["term_id"], ["id"], ondelete="RESTRICT")
        batch.drop_constraint("uq_exams_source_key", type_="unique")
        batch.create_unique_constraint("uq_exams_term_source_key", ["term_id", "source_key"])
        batch.create_index("ix_exams_term_id", ["term_id"])

    op.create_table(
        "workspace_states_v2",
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    connection.execute(sa.text(
        "INSERT INTO workspace_states_v2 (term_id, state_json, revision, updated_at) "
        "SELECT :term_id, state_json, revision, updated_at FROM workspace_states WHERE name = 'default'"
    ), {"term_id": term_id})
    op.drop_table("workspace_states")
    op.rename_table("workspace_states_v2", "workspace_states")
    connection.execute(sa.text(
        "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) "
        "VALUES ('active_term_id', :term_id, :now)"
    ), {"term_id": term_id, "now": now})


def downgrade() -> None:
    connection = op.get_bind()
    op.create_table(
        "workspace_states_v1",
        sa.Column("name", sa.String(50), primary_key=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    connection.execute(sa.text(
        "INSERT INTO workspace_states_v1 (name, state_json, revision, updated_at) "
        "SELECT 'default', state_json, revision, updated_at FROM workspace_states "
        "ORDER BY term_id LIMIT 1"
    ))
    op.drop_table("workspace_states")
    op.rename_table("workspace_states_v1", "workspace_states")
    with op.batch_alter_table("exams") as batch:
        batch.drop_index("ix_exams_term_id")
        batch.drop_constraint("uq_exams_term_source_key", type_="unique")
        batch.create_unique_constraint("uq_exams_source_key", ["source_key"])
        batch.drop_constraint("fk_exams_term_id", type_="foreignkey")
        batch.drop_column("term_id")
    op.drop_table("enrollments")
    with op.batch_alter_table("classes") as batch:
        batch.drop_index("ix_classes_term_id")
        batch.drop_constraint("uq_classes_term_name", type_="unique")
        batch.drop_constraint("fk_classes_term_id", type_="foreignkey")
        batch.create_unique_constraint("uq_classes_name", ["name"])
        batch.drop_column("term_id")
    connection.execute(sa.text("DELETE FROM app_settings WHERE key = 'active_term_id'"))
    op.drop_table("terms")
