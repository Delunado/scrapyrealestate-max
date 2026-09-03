"""Record safe outcomes from user-requested notification channel tests."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE notification_channel_tests (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL
                REFERENCES notification_channels(id) ON DELETE CASCADE,
            success INTEGER NOT NULL CHECK (success IN (0, 1)),
            error_category TEXT CHECK (
                error_category IS NULL OR length(trim(error_category)) > 0
            ),
            redacted_diagnostic TEXT CHECK (
                redacted_diagnostic IS NULL OR length(redacted_diagnostic) <= 2000
            ),
            tested_at TEXT NOT NULL
                CHECK (datetime(tested_at) IS NOT NULL AND substr(tested_at, -1) = 'Z'),
            CHECK (success = 0 OR (
                error_category IS NULL AND redacted_diagnostic IS NULL
            ))
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX notification_channel_tests_channel_time_idx
        ON notification_channel_tests(channel_id, tested_at DESC, id DESC)
        """
    )
