"""Create normalized listings with portal-scoped identity."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE listings (
            id INTEGER PRIMARY KEY,
            portal_key TEXT NOT NULL
                CHECK (
                    length(trim(portal_key)) > 0
                    AND portal_key = lower(portal_key)
                    AND portal_key NOT GLOB '*[^a-z0-9_]*'
                ),
            external_id TEXT CHECK (
                external_id IS NULL OR length(trim(external_id)) > 0
            ),
            canonical_url TEXT CHECK (canonical_url IS NULL OR (
                canonical_url GLOB 'http://*' OR canonical_url GLOB 'https://*'
            )),
            transaction_type TEXT NOT NULL CHECK (transaction_type IN ('buy', 'rent')),
            property_type TEXT NOT NULL DEFAULT 'unknown' CHECK (property_type IN (
                'apartment', 'house', 'land', 'commercial', 'office', 'garage',
                'storage', 'building', 'other', 'unknown'
            )),
            title TEXT NOT NULL CHECK (length(trim(title)) > 0),
            price_euros INTEGER CHECK (price_euros IS NULL OR price_euros >= 0),
            area_sqm REAL CHECK (area_sqm IS NULL OR area_sqm > 0),
            rooms INTEGER CHECK (rooms IS NULL OR rooms >= 0),
            bathrooms INTEGER CHECK (bathrooms IS NULL OR bathrooms >= 0),
            floor INTEGER,
            elevator TEXT NOT NULL DEFAULT 'unknown'
                CHECK (elevator IN ('yes', 'no', 'unknown')),
            terrace TEXT NOT NULL DEFAULT 'unknown'
                CHECK (terrace IN ('yes', 'no', 'unknown')),
            garage TEXT NOT NULL DEFAULT 'unknown'
                CHECK (garage IN ('yes', 'no', 'unknown')),
            location TEXT,
            neighbourhood TEXT,
            street TEXT,
            street_number TEXT,
            posted_at TEXT CHECK (posted_at IS NULL OR (
                datetime(posted_at) IS NOT NULL AND substr(posted_at, -1) = 'Z'
            )),
            first_seen_at TEXT NOT NULL
                CHECK (datetime(first_seen_at) IS NOT NULL AND substr(first_seen_at, -1) = 'Z'),
            last_seen_at TEXT NOT NULL
                CHECK (
                    datetime(last_seen_at) IS NOT NULL
                    AND substr(last_seen_at, -1) = 'Z'
                    AND last_seen_at >= first_seen_at
                ),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            raw_source_json TEXT NOT NULL DEFAULT '{}'
                CHECK (json_valid(raw_source_json) AND json_type(raw_source_json) = 'object'),
            CHECK (external_id IS NOT NULL OR canonical_url IS NOT NULL)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX listings_portal_external_id_uq
        ON listings(portal_key, external_id)
        WHERE external_id IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX listings_portal_canonical_url_uq
        ON listings(portal_key, canonical_url)
        WHERE canonical_url IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX listings_active_last_seen_idx
        ON listings(active, last_seen_at DESC)
        """
    )
