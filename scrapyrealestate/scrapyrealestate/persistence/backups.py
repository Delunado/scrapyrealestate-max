"""Integrity-checked SQLite backup, restore, and upgrade snapshots."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


class BackupIntegrityError(RuntimeError):
    """A source or newly copied SQLite database failed its integrity check."""


def backup_database(source: Path, destination: Path) -> Path:
    """Create a consistent SQLite backup without copying live WAL sidecars."""
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    with _read_only_connection(source) as connection:
        return backup_connection(connection, destination)


def backup_connection(connection: sqlite3.Connection, destination: Path) -> Path:
    """Snapshot an open connection to a new, atomically published file."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with closing(sqlite3.connect(temporary)) as copied:
            connection.backup(copied)
            _require_integrity(copied)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def restore_database(backup: Path, destination: Path, *, replace: bool = False) -> Path:
    """Validate and atomically restore a backup into a stopped application."""
    backup = Path(backup).resolve()
    destination = Path(destination).resolve()
    if backup == destination:
        raise ValueError("backup and restore destination must be different files")
    if not backup.is_file():
        raise FileNotFoundError(backup)
    if destination.exists() and not replace:
        raise FileExistsError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.restore")
    try:
        with _read_only_connection(backup) as source, closing(
            sqlite3.connect(temporary)
        ) as restored:
            _require_integrity(source)
            source.backup(restored)
            _require_integrity(restored)
        os.chmod(temporary, 0o600)
        for suffix in ("-wal", "-shm"):
            Path(f"{destination}{suffix}").unlink(missing_ok=True)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def create_pre_migration_backup(
    connection: sqlite3.Connection,
    backup_directory: Path,
    *,
    target_version: int,
) -> Path | None:
    """Snapshot an existing older schema once before forward migrations run."""
    current_version = schema_version(connection)
    if current_version >= target_version:
        return None
    destination = Path(backup_directory).resolve() / (
        f"pre-migration-v{current_version}-to-v{target_version}.sqlite3"
    )
    if destination.exists():
        with _read_only_connection(destination) as existing:
            _require_integrity(existing)
        return destination
    return backup_connection(connection, destination)


def schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if table is None:
        return 0
    return connection.execute(
        "SELECT coalesce(max(version), 0) FROM schema_migrations"
    ).fetchone()[0]


def _read_only_connection(path: Path):
    return closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True))


def _require_integrity(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    messages = tuple(str(row[0]) for row in rows)
    if messages != ("ok",):
        raise BackupIntegrityError("SQLite integrity check failed")
