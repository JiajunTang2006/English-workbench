from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .version import SCHEMA_REVISION


class Base(DeclarativeBase):
    pass


# 模块级全局 session factory，由 create_app 设置
# 供 config.py 等非路由模块在不通过依赖注入的情况下访问 DB
_global_session_factory: sessionmaker[Session] | None = None


def set_global_session_factory(factory: sessionmaker[Session] | None) -> None:
    """设置/清除全局 session factory。"""
    global _global_session_factory
    _global_session_factory = factory


def get_global_session_factory() -> sessionmaker[Session] | None:
    """获取全局 session factory（可能为 None，如测试环境未设置时）。"""
    return _global_session_factory


BASE_REVISION = "20260807_0001"
HEAD_REVISION = SCHEMA_REVISION
BASE_TABLES = {
    "classes", "students", "app_settings", "import_jobs",
    "backup_records", "change_logs",
}


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 5})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_schema(session_factory: sessionmaker[Session]) -> None:
    """仅供测试和一次性工具使用；应用启动必须使用 run_migrations。"""
    bind = session_factory.kw.get("bind")
    if bind is None:
        raise RuntimeError("Database engine is not configured")
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=bind)


def _alembic_config(database_url: str) -> Config:
    frozen_root = getattr(sys, "_MEIPASS", None)
    config_path = (Path(frozen_root) / "backend" / "alembic.ini") if frozen_root else Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def run_migrations(database_url: str) -> None:
    """升级数据库；兼容已由旧版 create_all 创建、但没有版本号的数据库。"""
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 5})
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    config = _alembic_config(database_url)
    if tables and "alembic_version" not in tables:
        unknown = tables - BASE_TABLES
        missing = BASE_TABLES - tables
        if unknown or missing:
            raise RuntimeError(
                f"无法自动识别未版本化数据库（缺少：{sorted(missing)}；未知：{sorted(unknown)}）"
            )
        command.stamp(config, BASE_REVISION)
    command.upgrade(config, "head")


def migration_needed(database_url: str) -> bool:
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 5})
    try:
        tables = set(inspect(engine).get_table_names())
        if not tables:
            return False
        if "alembic_version" not in tables:
            return True
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        return revision != HEAD_REVISION
    finally:
        engine.dispose()


def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
