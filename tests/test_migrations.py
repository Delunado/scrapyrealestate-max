import sqlite3
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations.runner import (
    Migration,
    MigrationHistoryError,
    MigrationRunner,
)


@pytest.fixture
def connection(tmp_path: Path):
    database = Database(tmp_path / "application.sqlite3")
    with database.connection() as opened:
        yield opened


def test_runner_applies_migrations_in_order_once(connection: sqlite3.Connection):
    applied = []

    def first(database):
        applied.append("first")
        database.execute("CREATE TABLE example (id INTEGER PRIMARY KEY) STRICT")

    def second(database):
        applied.append("second")
        database.execute("ALTER TABLE example ADD COLUMN name TEXT")

    runner = MigrationRunner(
        (Migration(1, "create_example", first), Migration(2, "add_name", second))
    )

    assert runner.migrate(connection) == 2
    assert runner.migrate(connection) == 2
    assert applied == ["first", "second"]
    assert [
        tuple(row)
        for row in connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )
    ] == [(1, "create_example"), (2, "add_name")]


def test_failed_migration_rolls_back_schema_and_history(connection: sqlite3.Connection):
    def fail(database):
        database.execute("CREATE TABLE should_not_remain (id INTEGER)")
        raise RuntimeError("migration failed")

    with pytest.raises(RuntimeError, match="migration failed"):
        MigrationRunner((Migration(1, "failure", fail),)).migrate(connection)

    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'should_not_remain'"
    ).fetchone() is None
    assert connection.execute("SELECT * FROM schema_migrations").fetchall() == []


def test_runner_rejects_changed_applied_history(connection: sqlite3.Connection):
    runner = MigrationRunner((Migration(1, "original", lambda database: None),))
    runner.migrate(connection)

    changed = MigrationRunner((Migration(1, "renamed", lambda database: None),))
    with pytest.raises(MigrationHistoryError, match="does not match"):
        changed.migrate(connection)


def test_runner_rejects_database_newer_than_application(connection: sqlite3.Connection):
    runner = MigrationRunner((Migration(1, "known", lambda database: None),))
    runner.migrate(connection)
    connection.execute(
        "INSERT INTO schema_migrations VALUES (2, 'future', '2026-01-01T00:00:00Z')"
    )

    with pytest.raises(MigrationHistoryError, match="newer"):
        runner.migrate(connection)


@pytest.mark.parametrize(
    "migrations",
    [
        (Migration(2, "gap", lambda database: None),),
        (
            Migration(1, "same", lambda database: None),
            Migration(2, "same", lambda database: None),
        ),
    ],
)
def test_runner_rejects_invalid_migration_definitions(migrations):
    with pytest.raises(ValueError):
        MigrationRunner(migrations)
