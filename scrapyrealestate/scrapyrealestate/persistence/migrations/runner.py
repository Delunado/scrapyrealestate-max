"""Transactional runner for immutable, ordered SQLite migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from scrapyrealestate.persistence.database import transaction


MigrationFunction = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable schema change identified by a monotonically increasing version."""

    version: int
    name: str
    apply: MigrationFunction

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("migration version must be positive")
        if not self.name.strip():
            raise ValueError("migration name is required")
        if not callable(self.apply):
            raise TypeError("migration apply must be callable")


class MigrationHistoryError(RuntimeError):
    """The database history is not a valid prefix of known migrations."""


class MigrationRunner:
    """Apply pending migrations exactly once and in version order."""

    def __init__(self, migrations: Iterable[Migration]) -> None:
        self.migrations = tuple(migrations)
        expected_versions = tuple(range(1, len(self.migrations) + 1))
        actual_versions = tuple(migration.version for migration in self.migrations)
        if actual_versions != expected_versions:
            raise ValueError("migrations must have contiguous versions starting at 1")
        names = tuple(migration.name for migration in self.migrations)
        if len(names) != len(set(names)):
            raise ValueError("migration names must be unique")

    def migrate(self, connection: sqlite3.Connection) -> int:
        """Apply pending migrations and return the resulting schema version."""
        self._ensure_history_table(connection)
        applied = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        self._validate_history(applied)

        for migration in self.migrations[len(applied) :]:
            with transaction(connection, immediate=True):
                migration.apply(connection)
                connection.execute(
                    """
                    INSERT INTO schema_migrations (version, name, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (migration.version, migration.name, _utc_now()),
                )
        return len(self.migrations)

    @staticmethod
    def _ensure_history_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
                applied_at TEXT NOT NULL
            ) STRICT
            """
        )

    def _validate_history(self, rows: list[sqlite3.Row]) -> None:
        if len(rows) > len(self.migrations):
            raise MigrationHistoryError("database schema is newer than this application")
        for index, row in enumerate(rows):
            expected = self.migrations[index]
            if row["version"] != expected.version or row["name"] != expected.name:
                raise MigrationHistoryError(
                    "database migration history does not match known migrations"
                )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
