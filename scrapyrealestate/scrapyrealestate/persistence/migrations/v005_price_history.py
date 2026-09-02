"""Create idempotent chronological listing price observations."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE listing_price_history (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
            price_euros INTEGER NOT NULL CHECK (price_euros >= 0),
            currency TEXT NOT NULL DEFAULT 'EUR'
                CHECK (
                    length(currency) = 3
                    AND currency = upper(currency)
                    AND currency NOT GLOB '*[^A-Z]*'
                ),
            observed_at TEXT NOT NULL
                CHECK (datetime(observed_at) IS NOT NULL AND substr(observed_at, -1) = 'Z'),
            UNIQUE (listing_id, observed_at)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX listing_price_history_chronology_idx
        ON listing_price_history(listing_id, observed_at DESC)
        """
    )
