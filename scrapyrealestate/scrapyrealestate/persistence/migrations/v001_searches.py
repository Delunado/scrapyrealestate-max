"""Create application settings, searches, and per-search schedules."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE application_settings (
            key TEXT PRIMARY KEY CHECK (length(trim(key)) > 0),
            value_json TEXT NOT NULL CHECK (json_valid(value_json)),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(updated_at) IS NOT NULL AND substr(updated_at, -1) = 'Z')
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE searches (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE
                CHECK (length(trim(name)) > 0),
            transaction_type TEXT NOT NULL CHECK (transaction_type IN ('buy', 'rent')),
            filters_json TEXT NOT NULL DEFAULT '{}'
                CHECK (json_valid(filters_json) AND json_type(filters_json) = 'object'),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(updated_at) IS NOT NULL AND substr(updated_at, -1) = 'Z'),
            UNIQUE (name)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE search_schedules (
            search_id INTEGER PRIMARY KEY REFERENCES searches(id) ON DELETE CASCADE,
            interval_seconds INTEGER NOT NULL CHECK (interval_seconds >= 300),
            next_run_at TEXT
                CHECK (next_run_at IS NULL OR (
                    datetime(next_run_at) IS NOT NULL AND substr(next_run_at, -1) = 'Z'
                )),
            last_scheduled_at TEXT
                CHECK (last_scheduled_at IS NULL OR (
                    datetime(last_scheduled_at) IS NOT NULL
                    AND substr(last_scheduled_at, -1) = 'Z'
                )),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(updated_at) IS NOT NULL AND substr(updated_at, -1) = 'Z')
        ) STRICT
        """
    )
    connection.execute(
        "CREATE INDEX search_schedules_next_run_idx ON search_schedules(next_run_at)"
    )
