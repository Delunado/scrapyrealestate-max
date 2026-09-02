"""Add explicit per-search notification event preferences."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE search_notification_preferences (
            search_id INTEGER PRIMARY KEY
                REFERENCES searches(id) ON DELETE CASCADE,
            notify_new_listing INTEGER NOT NULL DEFAULT 1
                CHECK (notify_new_listing IN (0, 1)),
            notify_price_drop INTEGER NOT NULL DEFAULT 1
                CHECK (notify_price_drop IN (0, 1)),
            notify_price_increase INTEGER NOT NULL DEFAULT 0
                CHECK (notify_price_increase IN (0, 1)),
            notify_reappearance INTEGER NOT NULL DEFAULT 0
                CHECK (notify_reappearance IN (0, 1)),
            updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(updated_at) IS NOT NULL AND substr(updated_at, -1) = 'Z')
        ) STRICT
        """
    )
