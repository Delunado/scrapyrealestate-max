import sqlite3
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


@pytest.fixture
def migrated_connection(tmp_path: Path):
    database = Database(tmp_path / "application.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        yield connection


def test_search_schema_is_created_at_version_one(migrated_connection):
    connection = migrated_connection
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert connection.execute(
        "SELECT max(version) FROM schema_migrations"
    ).fetchone()[0] == len(MIGRATIONS)

    search_id = connection.execute(
        """
        INSERT INTO searches (name, transaction_type, filters_json)
        VALUES ('Centro', 'buy', '{"min_price_euros": 100000}')
        RETURNING id
        """
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (?, 600)",
        (search_id,),
    )

    search = connection.execute("SELECT * FROM searches").fetchone()
    assert search["enabled"] == 1
    assert search["version"] == 1
    assert search["created_at"].endswith("Z")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO searches (name, transaction_type) VALUES ('', 'buy')",
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'lease')",
        "INSERT INTO searches (name, transaction_type, filters_json) "
        "VALUES ('A', 'buy', '[]')",
    ],
)
def test_search_constraints_reject_invalid_records(migrated_connection, statement):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(statement)


def test_schedule_requires_a_search_and_minimum_interval(migrated_connection):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (999, 600)"
        )

    search_id = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'rent') RETURNING id"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (?, 299)",
            (search_id,),
        )


def test_search_portal_schema_stores_validated_selection(migrated_connection):
    search_id = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
    ).fetchone()[0]
    migrated_connection.execute(
        """
        INSERT INTO search_portals (
            search_id, portal_key, raw_url_override, adapter_options_json, enabled
        ) VALUES (?, 'pisoscom', 'https://www.pisos.com/venta/pisos-madrid/',
                  '{"recent_sort": true}', 0)
        """,
        (search_id,),
    )

    selection = migrated_connection.execute("SELECT * FROM search_portals").fetchone()
    assert selection["portal_key"] == "pisoscom"
    assert selection["enabled"] == 0
    assert selection["created_at"].endswith("Z")


@pytest.mark.parametrize(
    ("portal_key", "raw_url", "options"),
    [
        ("Pisos", None, "{}"),
        ("pisos.com", None, "{}"),
        ("pisoscom", "ftp://example.com/results", "{}"),
        ("pisoscom", None, "[]"),
    ],
)
def test_search_portal_constraints_reject_invalid_values(
    migrated_connection, portal_key, raw_url, options
):
    search_id = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
    ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO search_portals (
                search_id, portal_key, raw_url_override, adapter_options_json
            ) VALUES (?, ?, ?, ?)
            """,
            (search_id, portal_key, raw_url, options),
        )


def _insert_listing(connection, portal_key="pisoscom", external_id="123", **values):
    defaults = {
        "canonical_url": "https://example.com/listing/123",
        "transaction_type": "buy",
        "title": "Piso céntrico",
        "first_seen_at": "2026-01-01T10:00:00Z",
        "last_seen_at": "2026-01-01T10:00:00Z",
    }
    defaults.update(values)
    return connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, canonical_url, transaction_type, title,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            portal_key,
            external_id,
            defaults["canonical_url"],
            defaults["transaction_type"],
            defaults["title"],
            defaults["first_seen_at"],
            defaults["last_seen_at"],
        ),
    ).fetchone()[0]


def test_listing_identity_is_scoped_to_portal(migrated_connection):
    first_id = _insert_listing(migrated_connection)
    second_id = _insert_listing(
        migrated_connection,
        portal_key="habitaclia",
        canonical_url="https://example.com/listing/123",
    )

    assert first_id != second_id
    with pytest.raises(sqlite3.IntegrityError):
        _insert_listing(
            migrated_connection,
            canonical_url="https://example.com/listing/different",
        )


def test_listing_canonical_url_is_a_portal_scoped_unique_fallback(
    migrated_connection,
):
    _insert_listing(migrated_connection, external_id=None)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_listing(migrated_connection, external_id=None)


@pytest.mark.parametrize(
    "values",
    [
        {"external_id": None, "canonical_url": None},
        {"title": " "},
        {"first_seen_at": "2026-01-02T10:00:00Z"},
        {"last_seen_at": "2026-01-01 10:00:00"},
    ],
)
def test_listing_constraints_reject_invalid_records(migrated_connection, values):
    external_id = values.pop("external_id", "123")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_listing(migrated_connection, external_id=external_id, **values)


def test_listing_can_match_many_searches_with_independent_seen_times(
    migrated_connection,
):
    first_search = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
    ).fetchone()[0]
    second_search = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('B', 'buy') RETURNING id"
    ).fetchone()[0]
    listing_id = _insert_listing(migrated_connection)

    for search_id, first_seen in (
        (first_search, "2026-01-01T10:00:00Z"),
        (second_search, "2026-01-02T10:00:00Z"),
    ):
        migrated_connection.execute(
            """
            INSERT INTO search_listing_matches (
                search_id, listing_id, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?)
            """,
            (search_id, listing_id, first_seen, "2026-01-03T10:00:00Z"),
        )

    rows = migrated_connection.execute(
        "SELECT * FROM search_listing_matches ORDER BY search_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["first_seen_at"] != rows[1]["first_seen_at"]
    assert all(row["active"] == 1 for row in rows)


def test_search_listing_match_enforces_identity_and_seen_order(migrated_connection):
    search_id = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
    ).fetchone()[0]
    listing_id = _insert_listing(migrated_connection)

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO search_listing_matches (
                search_id, listing_id, first_seen_at, last_seen_at
            ) VALUES (?, ?, '2026-01-02T10:00:00Z', '2026-01-01T10:00:00Z')
            """,
            (search_id, listing_id),
        )


def test_price_history_records_chronological_observations(migrated_connection):
    listing_id = _insert_listing(migrated_connection)
    migrated_connection.executemany(
        """
        INSERT INTO listing_price_history (listing_id, price_euros, observed_at)
        VALUES (?, ?, ?)
        """,
        (
            (listing_id, 200_000, "2026-01-01T10:00:00Z"),
            (listing_id, 195_000, "2026-01-02T10:00:00Z"),
            (listing_id, 195_000, "2026-01-03T10:00:00Z"),
        ),
    )

    rows = migrated_connection.execute(
        """
        SELECT price_euros, currency FROM listing_price_history
        WHERE listing_id = ? ORDER BY observed_at
        """,
        (listing_id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (200_000, "EUR"),
        (195_000, "EUR"),
        (195_000, "EUR"),
    ]


def test_price_history_prevents_duplicate_observation_times(migrated_connection):
    listing_id = _insert_listing(migrated_connection)
    migrated_connection.execute(
        """
        INSERT INTO listing_price_history (listing_id, price_euros, observed_at)
        VALUES (?, 200000, '2026-01-01T10:00:00Z')
        """,
        (listing_id,),
    )

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO listing_price_history (listing_id, price_euros, observed_at)
            VALUES (?, 190000, '2026-01-01T10:00:00Z')
            """,
            (listing_id,),
        )


@pytest.mark.parametrize(
    ("price", "currency", "observed_at"),
    [
        (-1, "EUR", "2026-01-01T10:00:00Z"),
        (1, "eur", "2026-01-01T10:00:00Z"),
        (1, "EURO", "2026-01-01T10:00:00Z"),
        (1, "EUR", "2026-01-01 10:00:00"),
    ],
)
def test_price_history_rejects_invalid_values(
    migrated_connection, price, currency, observed_at
):
    listing_id = _insert_listing(migrated_connection)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO listing_price_history (
                listing_id, price_euros, currency, observed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (listing_id, price, currency, observed_at),
        )


def _insert_search(connection, name="A"):
    return connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES (?, 'buy') RETURNING id",
        (name,),
    ).fetchone()[0]


def test_run_and_portal_attempt_capture_status_timing_and_counts(migrated_connection):
    search_id = _insert_search(migrated_connection)
    run_id = migrated_connection.execute(
        """
        INSERT INTO search_runs (
            search_id, trigger_kind, status, started_at, finished_at,
            returned_count, matched_count, new_count
        ) VALUES (?, 'manual', 'success', '2026-01-01T10:00:00Z',
                  '2026-01-01T10:01:00Z', 10, 8, 3)
        RETURNING id
        """,
        (search_id,),
    ).fetchone()[0]
    migrated_connection.execute(
        """
        INSERT INTO portal_attempts (
            search_run_id, portal_key, status, started_at, finished_at,
            returned_count, matched_count, new_count
        ) VALUES (?, 'pisoscom', 'success', '2026-01-01T10:00:00Z',
                  '2026-01-01T10:01:00Z', 10, 8, 3)
        """,
        (run_id,),
    )

    attempt = migrated_connection.execute("SELECT * FROM portal_attempts").fetchone()
    assert attempt["returned_count"] == 10
    assert attempt["matched_count"] == 8
    assert attempt["new_count"] == 3
    assert attempt["attempt_number"] == 1


def test_failed_portal_attempt_requires_finish_and_accepts_bounded_diagnostic(
    migrated_connection,
):
    search_id = _insert_search(migrated_connection)
    run_id = migrated_connection.execute(
        """
        INSERT INTO search_runs (search_id, trigger_kind, status, started_at)
        VALUES (?, 'scheduled', 'running', '2026-01-01T10:00:00Z') RETURNING id
        """,
        (search_id,),
    ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO portal_attempts (
                search_run_id, portal_key, status, started_at, error_category
            ) VALUES (?, 'idealista', 'blocked', '2026-01-01T10:00:00Z', 'challenge')
            """,
            (run_id,),
        )

    migrated_connection.execute(
        """
        INSERT INTO portal_attempts (
            search_run_id, portal_key, status, started_at, finished_at,
            error_category, redacted_diagnostic
        ) VALUES (?, 'idealista', 'blocked', '2026-01-01T10:00:00Z',
                  '2026-01-01T10:00:05Z', 'challenge', 'DataDome challenge')
        """,
        (run_id,),
    )


@pytest.mark.parametrize(
    "statement",
    [
        """
        INSERT INTO search_runs (search_id, trigger_kind, status)
        VALUES (1, 'startup', 'pending')
        """,
        """
        INSERT INTO search_runs (search_id, trigger_kind, status, started_at)
        VALUES (1, 'manual', 'success', '2026-01-01T10:00:00Z')
        """,
        """
        INSERT INTO search_runs (
            search_id, trigger_kind, status, started_at, returned_count
        ) VALUES (1, 'manual', 'running', '2026-01-01T10:00:00Z', -1)
        """,
    ],
)
def test_search_run_constraints_reject_invalid_state(migrated_connection, statement):
    _insert_search(migrated_connection)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(statement)


def _insert_channel(connection, name="Telegram"):
    return connection.execute(
        """
        INSERT INTO notification_channels (
            name, provider, config_json, secret_config_json
        ) VALUES (?, 'telegram', '{"chat_id": "123"}', '{"bot_token": "secret"}')
        RETURNING id
        """,
        (name,),
    ).fetchone()[0]


def test_notification_channel_can_be_assigned_to_multiple_searches(
    migrated_connection,
):
    channel_id = _insert_channel(migrated_connection)
    first_search = _insert_search(migrated_connection, "A")
    second_search = _insert_search(migrated_connection, "B")
    migrated_connection.executemany(
        """
        INSERT INTO search_notification_channels (search_id, channel_id)
        VALUES (?, ?)
        """,
        ((first_search, channel_id), (second_search, channel_id)),
    )

    assert migrated_connection.execute(
        "SELECT count(*) FROM search_notification_channels WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()[0] == 2


def test_provider_neutral_event_has_retry_identified_delivery_attempts(
    migrated_connection,
):
    search_id = _insert_search(migrated_connection)
    listing_id = _insert_listing(migrated_connection)
    channel_id = _insert_channel(migrated_connection)
    event_id = migrated_connection.execute(
        """
        INSERT INTO notification_events (
            search_id, listing_id, event_type, deduplication_key,
            payload_json, occurred_at
        ) VALUES (?, ?, 'price_drop', 'price-drop:pisoscom:123:195000',
                  '{"old_price": 200000, "new_price": 195000}',
                  '2026-01-01T10:00:00Z')
        RETURNING id
        """,
        (search_id, listing_id),
    ).fetchone()[0]
    migrated_connection.executemany(
        """
        INSERT INTO notification_delivery_attempts (
            event_id, channel_id, attempt_number, status, claimed_at,
            completed_at, error_category, redacted_diagnostic
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                event_id,
                channel_id,
                1,
                "failed",
                "2026-01-01T10:01:00Z",
                "2026-01-01T10:01:01Z",
                "timeout",
                "provider timed out",
            ),
            (event_id, channel_id, 2, "pending", None, None, None, None),
        ),
    )

    attempts = migrated_connection.execute(
        """
        SELECT attempt_number, status FROM notification_delivery_attempts
        ORDER BY attempt_number
        """
    ).fetchall()
    assert [tuple(row) for row in attempts] == [(1, "failed"), (2, "pending")]

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO notification_delivery_attempts (
                event_id, channel_id, attempt_number
            ) VALUES (?, ?, 2)
            """,
            (event_id, channel_id),
        )


@pytest.mark.parametrize(
    "statement",
    [
        """
        INSERT INTO notification_channels (name, provider)
        VALUES ('Unknown', 'email')
        """,
        """
        INSERT INTO notification_channels (name, provider, config_json)
        VALUES ('Bad JSON', 'ntfy', '[]')
        """,
    ],
)
def test_notification_channel_constraints_reject_invalid_values(
    migrated_connection, statement
):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(statement)


def test_notification_event_deduplication_key_is_unique(migrated_connection):
    search_id = _insert_search(migrated_connection)
    values = (search_id, "new-listing:pisoscom:123", "2026-01-01T10:00:00Z")
    migrated_connection.execute(
        """
        INSERT INTO notification_events (
            search_id, event_type, deduplication_key, occurred_at
        ) VALUES (?, 'new_listing', ?, ?)
        """,
        values,
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO notification_events (
                search_id, event_type, deduplication_key, occurred_at
            ) VALUES (?, 'new_listing', ?, ?)
            """,
            values,
        )


def test_notification_preferences_enforce_per_search_boolean_selection(
    migrated_connection,
):
    search_id = _insert_search(migrated_connection)
    migrated_connection.execute(
        "INSERT INTO search_notification_preferences (search_id) VALUES (?)",
        (search_id,),
    )
    row = migrated_connection.execute(
        "SELECT * FROM search_notification_preferences WHERE search_id = ?",
        (search_id,),
    ).fetchone()
    assert (
        row["notify_new_listing"],
        row["notify_price_drop"],
        row["notify_price_increase"],
        row["notify_reappearance"],
    ) == (1, 1, 0, 0)

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            UPDATE search_notification_preferences
            SET notify_price_increase = 2 WHERE search_id = ?
            """,
            (search_id,),
        )

    migrated_connection.execute("DELETE FROM searches WHERE id = ?", (search_id,))
    assert migrated_connection.execute(
        "SELECT count(*) FROM search_notification_preferences"
    ).fetchone()[0] == 0


def test_delivery_claim_schema_supports_leases_and_scheduled_retries(
    migrated_connection,
):
    search_id = _insert_search(migrated_connection)
    channel_id = _insert_channel(migrated_connection)
    event_id = migrated_connection.execute(
        """
        INSERT INTO notification_events (
            search_id, event_type, deduplication_key, occurred_at
        ) VALUES (?, 'new_listing', 'claim-schema', '2026-01-01T10:00:00Z')
        RETURNING id
        """,
        (search_id,),
    ).fetchone()[0]
    migrated_connection.execute(
        """
        INSERT INTO notification_delivery_attempts (
            event_id, channel_id, available_at, status, claimed_at,
            claim_token, lease_expires_at
        ) VALUES (?, ?, '2026-01-01T10:01:00Z', 'claimed',
                  '2026-01-01T10:01:01Z', 'claim-1', '2026-01-01T10:02:01Z')
        """,
        (event_id, channel_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO notification_delivery_attempts (
                event_id, channel_id, attempt_number, available_at, status,
                claimed_at, claim_token, lease_expires_at
            ) VALUES (?, ?, 2, '2026-01-01T10:01:00Z', 'claimed',
                      '2026-01-01T10:01:01Z', 'claim-1', '2026-01-01T10:02:01Z')
            """,
            (event_id, channel_id),
        )
