from __future__ import annotations

from alembic import command
from sqlalchemy import create_engine, inspect, text

from backend.app.database import _alembic_config, run_migrations
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.version import SCHEMA_REVISION


def current_revision(database_url: str) -> str:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def test_empty_database_upgrades_to_head(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'empty.db'}"
    run_migrations(database_url)
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        exam_score_columns = {item["name"] for item in inspector.get_columns("exam_scores")}
        enrollment_columns = {item["name"] for item in inspector.get_columns("enrollments")}
    finally:
        engine.dispose()
    assert {"terms", "classes", "enrollments", "students", "exams", "exam_scores", "exam_class_metrics", "score_dimensions", "workspace_states"} <= tables
    assert "grade_rank" in exam_score_columns
    assert {"entrance_english", "target_score", "weak_tags", "parent_phone", "seat"} <= enrollment_columns
    assert current_revision(database_url) == SCHEMA_REVISION


def test_unversioned_legacy_database_is_stamped_then_upgraded(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260807_0001")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))
    finally:
        engine.dispose()

    run_migrations(database_url)
    assert current_revision(database_url) == SCHEMA_REVISION


def test_app_factory_creates_backup_before_legacy_upgrade(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'workbench.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260807_0001")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))
    finally:
        engine.dispose()

    create_app(Settings(data_dir=tmp_path))
    backups = list((tmp_path / "backups").glob("pre_migration_*"))
    assert len(backups) == 1
    assert (backups[0] / "workbench.db").is_file()
    assert current_revision(database_url) == SCHEMA_REVISION


def test_enrollment_profile_migration_preserves_existing_student_values(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'term_profile.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260809_0006")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            term_id = connection.execute(text(
                "SELECT id FROM terms ORDER BY id LIMIT 1"
            )).scalar_one()
            connection.execute(text(
                "INSERT INTO classes (term_id, name, status, created_at, updated_at) "
                "VALUES (:term_id, '711', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"term_id": term_id})
            class_id = connection.execute(text("SELECT id FROM classes WHERE name = '711'" )).scalar_one()
            connection.execute(text(
                "INSERT INTO students "
                "(student_no, name, class_id, target_score, weak_tags, parent_phone, seat, status, created_at, updated_at) "
                "VALUES ('01', '张三', :class_id, 90, '阅读', '13800000000', '12', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"class_id": class_id})
            student_id = connection.execute(text("SELECT id FROM students WHERE student_no = '01'" )).scalar_one()
            connection.execute(text(
                "INSERT INTO enrollments "
                "(term_id, class_id, student_id, status, created_at, updated_at) "
                "VALUES (:term_id, :class_id, :student_id, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"term_id": term_id, "class_id": class_id, "student_id": student_id})
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT target_score, weak_tags, parent_phone, seat FROM enrollments"
            )).mappings().one()
    finally:
        engine.dispose()
    assert dict(row) == {
        "target_score": 90.0,
        "weak_tags": "阅读",
        "parent_phone": "13800000000",
        "seat": "12",
    }
