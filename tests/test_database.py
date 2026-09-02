import sqlite3
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database, transaction


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "nested" / "application.sqlite3", busy_timeout_ms=3210)


def test_connection_enables_expected_sqlite_settings(database: Database):
    with database.connection() as connection:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 3210
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_transaction_commits_successful_work(database: Database):
    with database.connection() as connection:
        connection.execute("CREATE TABLE example (value TEXT NOT NULL)")
        with transaction(connection):
            connection.execute("INSERT INTO example VALUES ('saved')")

        assert connection.execute("SELECT value FROM example").fetchone()[0] == "saved"


def test_transaction_rolls_back_failed_work(database: Database):
    with database.connection() as connection:
        connection.execute("CREATE TABLE example (value TEXT NOT NULL)")

        with pytest.raises(RuntimeError, match="stop"):
            with transaction(connection):
                connection.execute("INSERT INTO example VALUES ('discarded')")
                raise RuntimeError("stop")

        assert connection.execute("SELECT value FROM example").fetchall() == []


def test_foreign_key_constraints_are_enforced(database: Database):
    with database.connection() as connection:
        connection.executescript(
            """
            CREATE TABLE parent (id INTEGER PRIMARY KEY);
            CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO child VALUES (999)")


def test_database_rejects_negative_busy_timeout(tmp_path: Path):
    with pytest.raises(ValueError, match="cannot be negative"):
        Database(tmp_path / "application.sqlite3", busy_timeout_ms=-1)
