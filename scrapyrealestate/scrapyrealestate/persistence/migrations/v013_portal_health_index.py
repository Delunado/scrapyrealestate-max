"""Index recent portal attempts for bounded health summaries."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX portal_attempts_portal_recent_idx
        ON portal_attempts(portal_key, started_at DESC, id DESC)
        """
    )
