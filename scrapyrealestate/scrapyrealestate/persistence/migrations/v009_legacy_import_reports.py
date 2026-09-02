"""Create durable legacy migration reports and rollback-source markers."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE legacy_import_reports (
            import_key TEXT PRIMARY KEY CHECK (length(trim(import_key)) > 0),
            source_name TEXT NOT NULL CHECK (length(trim(source_name)) > 0),
            source_digest TEXT NOT NULL CHECK (
                length(source_digest) = 64
                AND source_digest = lower(source_digest)
                AND source_digest NOT GLOB '*[^a-f0-9]*'
            ),
            rollback_source_marker TEXT NOT NULL
                CHECK (length(trim(rollback_source_marker)) > 0),
            source_preserved INTEGER NOT NULL CHECK (source_preserved IN (0, 1)),
            imported_records INTEGER NOT NULL CHECK (imported_records >= 0),
            ignored_records INTEGER NOT NULL DEFAULT 0 CHECK (ignored_records >= 0),
            warnings_json TEXT NOT NULL DEFAULT '[]'
                CHECK (json_valid(warnings_json) AND json_type(warnings_json) = 'array'),
            completed_at TEXT NOT NULL
                CHECK (datetime(completed_at) IS NOT NULL AND substr(completed_at, -1) = 'Z')
        ) STRICT
        """
    )
