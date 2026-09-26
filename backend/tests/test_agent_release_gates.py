from __future__ import annotations

from sqlalchemy import inspect

from backend.app.agent.config import get_agent_config
from backend.app.database import create_session_factory, run_migrations
from backend.app.models.agent_entities import AgentMessage
from backend.app.version import SCHEMA_REVISION


def test_feature_flags_can_be_enabled_from_environment(monkeypatch):
    monkeypatch.setenv("AGENT_ENABLED", "true")
    monkeypatch.setenv("AGENT_TEXT_ENABLED", "1")
    monkeypatch.setenv("AGENT_VISION_ANALYSIS_ENABLED", "yes")
    config = get_agent_config()
    assert config.agent_enabled is True
    assert config.is_feature_enabled("text_agent_enabled") is True
    assert config.is_feature_enabled("vision_analysis_enabled") is True


def test_agent_migration_is_real_head_and_contains_runtime_tables(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    run_migrations(database_url)
    factory = create_session_factory(database_url)
    engine = factory.kw["bind"]
    tables = set(inspect(engine).get_table_names())
    assert {
        "agent_sessions", "agent_messages", "analysis_runs",
        "analysis_evidence", "llm_usage_records", "background_jobs",
    } <= tables
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == SCHEMA_REVISION


def test_agent_message_schema_has_run_association():
    assert "analysis_run_id" in AgentMessage.__table__.c
