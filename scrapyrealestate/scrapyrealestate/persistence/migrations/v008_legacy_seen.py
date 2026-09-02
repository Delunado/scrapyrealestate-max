"""Create conservative portal-unscoped legacy seen-ID storage."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE legacy_seen_ids (
            legacy_id INTEGER PRIMARY KEY CHECK (legacy_id >= 0),
            imported_at TEXT NOT NULL
                CHECK (datetime(imported_at) IS NOT NULL AND substr(imported_at, -1) = 'Z')
        ) STRICT
        """
    )
