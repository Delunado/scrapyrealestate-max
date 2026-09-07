"""Index the bounded structural/location query used for duplicate candidates."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX listings_duplicate_narrowing_idx
        ON listings(
            transaction_type,
            location COLLATE NOCASE,
            rooms,
            property_type,
            portal_key,
            id
        )
        WHERE location IS NOT NULL
        """
    )
