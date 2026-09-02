"""Configured SQLite connections and explicit transaction handling."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BUSY_TIMEOUT_MS = 5_000


@dataclass(frozen=True, slots=True)
class Database:
    """Factory for consistently configured SQLite connections."""

    path: Path
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS

    def __post_init__(self) -> None:
        path = Path(self.path)
        if self.busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms cannot be negative")
        object.__setattr__(self, "path", path)

    def connect(self) -> sqlite3.Connection:
        """Open a connection with application-wide safety settings."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and always close it afterwards."""
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, immediate: bool = False
) -> Iterator[sqlite3.Connection]:
    """Commit a unit of work, rolling it back when an exception escapes."""
    if connection.in_transaction:
        raise RuntimeError("nested transactions are not supported")
    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
