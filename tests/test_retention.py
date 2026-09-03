from datetime import datetime, timezone
from pathlib import Path

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.services.retention import (
    OperationalRetentionService,
    RetentionPolicy,
)


def test_retention_prunes_only_verbose_and_terminal_operational_history(
    tmp_path: Path,
):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
        ).fetchone()[0]
        old_run = _insert_run(connection, search_id, "2026-07-01T10:00:00Z")
        recent_run = _insert_run(connection, search_id, "2026-08-20T10:00:00Z")
        _insert_attempt(connection, old_run, "2026-07-01T10:00:00Z")
        _insert_attempt(connection, recent_run, "2026-08-20T10:00:00Z")
        listing_id = connection.execute(
            """
            INSERT INTO listings (
                portal_key, external_id, transaction_type, title,
                first_seen_at, last_seen_at
            ) VALUES ('pisoscom', 'retained', 'buy', 'Retained',
                      '2026-05-01T10:00:00Z', '2026-05-01T10:00:00Z') RETURNING id
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO listing_price_history (listing_id, price_euros, observed_at)
            VALUES (?, 100000, '2026-05-01T10:00:00Z')
            """,
            (listing_id,),
        )
        channel_id = connection.execute(
            "INSERT INTO notification_channels (name, provider) VALUES ('N', 'ntfy') RETURNING id"
        ).fetchone()[0]
        event_id = connection.execute(
            """
            INSERT INTO notification_events (
                search_id, listing_id, event_type, deduplication_key, occurred_at
            ) VALUES (?, ?, 'new_listing', 'retention:event', '2026-05-01T10:00:00Z')
            RETURNING id
            """,
            (search_id, listing_id),
        ).fetchone()[0]
        for number, completed_at in enumerate(
            (
                "2026-05-01T10:00:00Z",
                "2026-07-01T10:00:00Z",
                "2026-08-01T10:00:00Z",
                "2026-09-01T10:00:00Z",
            ),
            start=1,
        ):
            _insert_delivery(
                connection, event_id, channel_id, number, "succeeded", completed_at
            )
        _insert_delivery(connection, event_id, channel_id, 5, "pending", None)

        outcome = OperationalRetentionService(
            connection,
            RetentionPolicy(
                diagnostic_days=30,
                delivery_attempt_days=90,
                max_terminal_delivery_attempts=2,
            ),
        ).prune(now=datetime(2026, 9, 3, tzinfo=timezone.utc))

        assert outcome.run_diagnostics_cleared == 1
        assert outcome.attempt_diagnostics_cleared == 1
        assert outcome.delivery_attempts_deleted == 2
        assert connection.execute(
            "SELECT redacted_diagnostic FROM search_runs WHERE id = ?", (old_run,)
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT redacted_diagnostic FROM search_runs WHERE id = ?", (recent_run,)
        ).fetchone()[0] == "safe diagnostic"
        attempts = connection.execute(
            "SELECT status FROM notification_delivery_attempts ORDER BY id"
        ).fetchall()
        assert [row["status"] for row in attempts] == [
            "succeeded",
            "succeeded",
            "pending",
        ]
        assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM listing_price_history"
        ).fetchone()[0] == 1


def _insert_run(connection, search_id, timestamp):
    return connection.execute(
        """
        INSERT INTO search_runs (
            search_id, trigger_kind, status, started_at, finished_at,
            error_category, redacted_diagnostic, created_at
        ) VALUES (?, 'manual', 'failed', ?, ?, 'failed', 'safe diagnostic', ?)
        RETURNING id
        """,
        (search_id, timestamp, timestamp, timestamp),
    ).fetchone()[0]


def _insert_attempt(connection, run_id, timestamp):
    connection.execute(
        """
        INSERT INTO portal_attempts (
            search_run_id, portal_key, status, started_at, finished_at,
            error_category, redacted_diagnostic
        ) VALUES (?, 'pisoscom', 'parser_error', ?, ?, 'parser_error',
                  'safe attempt diagnostic')
        """,
        (run_id, timestamp, timestamp),
    )


def _insert_delivery(
    connection, event_id, channel_id, attempt_number, status, completed_at
):
    connection.execute(
        """
        INSERT INTO notification_delivery_attempts (
            event_id, channel_id, attempt_number, status, claimed_at,
            completed_at, available_at
        ) VALUES (?, ?, ?, ?, ?, ?, '2026-05-01T10:00:00Z')
        """,
        (
            event_id,
            channel_id,
            attempt_number,
            status,
            completed_at,
            completed_at,
        ),
    )
