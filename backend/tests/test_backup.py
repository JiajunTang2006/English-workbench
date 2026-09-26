from __future__ import annotations

import sqlite3

import pytest

from tools.verify_backup import create_backup, verify_backup
from backend.app.services.backups import restore_backup


def test_backup_api_captures_wal_data_and_verifies_integrity(tmp_path):
    database = tmp_path / "workbench.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    connection.execute("INSERT INTO students(name) VALUES (?)", ("测试学生",))
    connection.commit()

    destination = create_backup(database, tmp_path / "backup", kind="test")
    result = verify_backup(destination)

    assert result["valid"] is True
    assert result["integrity"] == "ok"
    assert result["manifest"]["kind"] == "test"
    with sqlite3.connect(destination / "workbench.db") as restored:
        assert restored.execute("SELECT name FROM students").fetchone()[0] == "测试学生"
    connection.close()


def test_verify_backup_rejects_tampering(tmp_path):
    database = tmp_path / "workbench.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")

    destination = create_backup(database, tmp_path / "backup")
    with (destination / "workbench.db").open("ab") as file:
        file.write(b"tampered")

    with pytest.raises(ValueError, match="校验和不匹配"):
        verify_backup(destination)


def test_restore_backup_replaces_database_and_keeps_safety_copy(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('backup')")

    backup = create_backup(source, tmp_path / "backup", kind="test")
    data_dir = tmp_path / "live"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "workbench.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('live')")

    safety = restore_backup(backup, data_dir)

    with sqlite3.connect(data_dir / "workbench.db") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "backup"
    with sqlite3.connect(safety / "workbench.db") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "live"


def test_restore_rejects_invalid_backup_without_changing_live_data(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    backup = create_backup(source, tmp_path / "backup")
    (backup / "workbench.db").write_bytes(b"broken")
    data_dir = tmp_path / "live"
    data_dir.mkdir()
    live = data_dir / "workbench.db"
    live.write_bytes(b"unchanged")

    with pytest.raises(ValueError):
        restore_backup(backup, data_dir)

    assert live.read_bytes() == b"unchanged"
