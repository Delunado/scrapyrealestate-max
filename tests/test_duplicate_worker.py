import sqlite3
from pathlib import Path

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.services.duplicate_worker import DuplicateCandidateWorker


def _listing(connection, portal, external_id, price):
    return connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, canonical_url, transaction_type,
            property_type, title, price_euros, area_sqm, rooms, location,
            neighbourhood, street, street_number, first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, 'buy', 'apartment',
                  'Piso luminoso reformado con terraza', ?, 100, 3, 'Madrid',
                  'Chamberí', 'Calle Santa Engracia', '42',
                  '2026-09-07T09:00:00Z', '2026-09-07T09:00:00Z')
        RETURNING id
        """,
        (portal, external_id, f"https://example.com/{portal}/{external_id}", price),
    ).fetchone()[0]


def test_worker_scores_and_persists_candidates_after_submission(tmp_path: Path):
    database = Database(tmp_path / "worker.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        first = _listing(connection, "pisoscom", "1", 300_000)
        second = _listing(connection, "habitaclia", "2", 304_000)

    worker = DuplicateCandidateWorker(database)
    worker.start()
    try:
        assert worker.submit((second,)) is True
        assert worker.wait_until_idle(timeout=2.0) is True
    finally:
        assert worker.shutdown(timeout=2.0) is True

    with database.connection() as connection:
        group = connection.execute("SELECT * FROM duplicate_candidate_groups").fetchone()
        assert group["pair_key"] == f"{first}:{second}"
        assert group["review_state"] == "pending"
        assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 2


def test_worker_submission_is_non_blocking_and_shutdown_rejects_new_work(tmp_path: Path):
    database = Database(tmp_path / "nonblocking.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
    worker = DuplicateCandidateWorker(database)
    worker.start()

    assert worker.submit((999,)) is True
    assert worker.wait_until_idle(timeout=2.0) is True
    assert worker.shutdown(timeout=2.0) is True
    assert worker.submit((999,)) is False

    with database.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM duplicate_candidate_groups"
        ).fetchone()[0] == 0


def test_worker_does_not_share_bootstrap_sqlite_connection(tmp_path: Path):
    database = Database(tmp_path / "connections.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        worker = DuplicateCandidateWorker(database)
        worker.start()
        assert worker.shutdown(timeout=2.0) is True
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with database.connection() as connection:
        assert isinstance(connection, sqlite3.Connection)
