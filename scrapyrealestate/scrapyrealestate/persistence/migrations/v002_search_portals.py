"""Create per-search portal selections and adapter configuration."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE search_portals (
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            portal_key TEXT NOT NULL
                CHECK (
                    length(trim(portal_key)) > 0
                    AND portal_key = lower(portal_key)
                    AND portal_key NOT GLOB '*[^a-z0-9_]*'
                ),
            raw_url_override TEXT
                CHECK (raw_url_override IS NULL OR (
                    raw_url_override GLOB 'http://*'
                    OR raw_url_override GLOB 'https://*'
                )),
            adapter_options_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    json_valid(adapter_options_json)
                    AND json_type(adapter_options_json) = 'object'
                ),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(updated_at) IS NOT NULL AND substr(updated_at, -1) = 'Z'),
            PRIMARY KEY (search_id, portal_key)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX search_portals_enabled_idx
        ON search_portals(search_id, enabled, portal_key)
        """
    )
