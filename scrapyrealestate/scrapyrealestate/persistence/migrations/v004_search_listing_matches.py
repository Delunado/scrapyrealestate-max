"""Create many-to-many search/listing matches and visibility metadata."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE search_listing_matches (
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
            first_seen_at TEXT NOT NULL
                CHECK (datetime(first_seen_at) IS NOT NULL AND substr(first_seen_at, -1) = 'Z'),
            last_seen_at TEXT NOT NULL
                CHECK (
                    datetime(last_seen_at) IS NOT NULL
                    AND substr(last_seen_at, -1) = 'Z'
                    AND last_seen_at >= first_seen_at
                ),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            PRIMARY KEY (search_id, listing_id)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX search_listing_matches_listing_idx
        ON search_listing_matches(listing_id, search_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX search_listing_matches_active_idx
        ON search_listing_matches(search_id, active, last_seen_at DESC)
        """
    )
