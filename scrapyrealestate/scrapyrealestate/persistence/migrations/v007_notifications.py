"""Create provider-neutral notification configuration, events, and delivery attempts."""

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE notification_channels (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(name)) > 0),
            provider TEXT NOT NULL CHECK (provider IN ('telegram', 'ntfy', 'webhook')),
            config_json TEXT NOT NULL DEFAULT '{}'
                CHECK (json_valid(config_json) AND json_type(config_json) = 'object'),
            secret_config_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    json_valid(secret_config_json)
                    AND json_type(secret_config_json) = 'object'
                ),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
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
        CREATE TABLE search_notification_channels (
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            channel_id INTEGER NOT NULL
                REFERENCES notification_channels(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            PRIMARY KEY (search_id, channel_id)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX search_notification_channels_channel_idx
        ON search_notification_channels(channel_id, search_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE notification_events (
            id INTEGER PRIMARY KEY,
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            listing_id INTEGER REFERENCES listings(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'new_listing', 'price_drop', 'price_increase', 'reappearance'
            )),
            deduplication_key TEXT NOT NULL UNIQUE
                CHECK (length(trim(deduplication_key)) > 0),
            payload_json TEXT NOT NULL DEFAULT '{}'
                CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
            occurred_at TEXT NOT NULL
                CHECK (datetime(occurred_at) IS NOT NULL AND substr(occurred_at, -1) = 'Z'),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z')
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX notification_events_search_time_idx
        ON notification_events(search_id, occurred_at DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE notification_delivery_attempts (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL
                REFERENCES notification_events(id) ON DELETE CASCADE,
            channel_id INTEGER NOT NULL
                REFERENCES notification_channels(id) ON DELETE RESTRICT,
            attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                'pending', 'claimed', 'succeeded', 'failed'
            )),
            claimed_at TEXT CHECK (claimed_at IS NULL OR (
                datetime(claimed_at) IS NOT NULL AND substr(claimed_at, -1) = 'Z'
            )),
            completed_at TEXT CHECK (completed_at IS NULL OR (
                datetime(completed_at) IS NOT NULL
                AND substr(completed_at, -1) = 'Z'
                AND (claimed_at IS NULL OR completed_at >= claimed_at)
            )),
            error_category TEXT CHECK (
                error_category IS NULL OR length(trim(error_category)) > 0
            ),
            redacted_diagnostic TEXT CHECK (
                redacted_diagnostic IS NULL OR length(redacted_diagnostic) <= 2000
            ),
            provider_message_id TEXT,
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                CHECK (datetime(created_at) IS NOT NULL AND substr(created_at, -1) = 'Z'),
            UNIQUE (event_id, channel_id, attempt_number),
            CHECK (status != 'claimed' OR claimed_at IS NOT NULL),
            CHECK (status NOT IN ('succeeded', 'failed') OR completed_at IS NOT NULL)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE INDEX notification_delivery_attempts_pending_idx
        ON notification_delivery_attempts(status, created_at, id)
        """
    )
    connection.execute(
        """
        CREATE INDEX notification_delivery_attempts_event_idx
        ON notification_delivery_attempts(event_id, channel_id, attempt_number DESC)
        """
    )
