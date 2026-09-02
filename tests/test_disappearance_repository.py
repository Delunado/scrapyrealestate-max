from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.listings import ListingMatchRepository
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


@pytest.fixture
def setup(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        first_search = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
        ).fetchone()[0]
        second_search = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('B', 'buy') RETURNING id"
        ).fetchone()[0]
        yield ListingMatchRepository(connection), connection, first_search, second_search


def _listing(portal: PortalKey, external_id: str) -> NormalizedListing:
    return NormalizedListing(
        portal=portal,
        external_id=external_id,
        transaction_type=TransactionType.BUY,
        title=f"Listing {external_id}",
        observed_at=datetime(2026, 9, 1, 10, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.TIMEOUT,
        RunStatus.TRANSPORT_ERROR,
        RunStatus.PARSER_ERROR,
        RunStatus.BLOCKED,
        RunStatus.UNAVAILABLE,
    ],
)
def test_inconclusive_attempts_never_mark_matches_absent(setup, status):
    repository, connection, search_id, _ = setup
    listing_id = repository.ingest(
        search_id, _listing(PortalKey.IDEALISTA, "1")
    ).listing_id

    result = repository.reconcile_portal(
        search_id, PortalKey.IDEALISTA, set(), status
    )

    assert result.reconciled is False
    assert connection.execute(
        "SELECT active FROM search_listing_matches WHERE listing_id = ?", (listing_id,)
    ).fetchone()[0] == 1


@pytest.mark.parametrize("status", [RunStatus.SUCCESS, RunStatus.EMPTY])
def test_conclusive_attempt_deactivates_only_unseen_portal_matches(setup, status):
    repository, connection, search_id, _ = setup
    seen_id = repository.ingest(
        search_id, _listing(PortalKey.PISOSCOM, "seen")
    ).listing_id
    missing_id = repository.ingest(
        search_id, _listing(PortalKey.PISOSCOM, "missing")
    ).listing_id
    other_portal_id = repository.ingest(
        search_id, _listing(PortalKey.HABITACLIA, "other")
    ).listing_id

    result = repository.reconcile_portal(
        search_id, PortalKey.PISOSCOM, {seen_id}, status
    )

    assert result.inactive_match_ids == (missing_id,)
    assert result.inactive_listing_ids == (missing_id,)
    active = dict(
        connection.execute(
            "SELECT id, active FROM listings ORDER BY id"
        ).fetchall()
    )
    assert active == {seen_id: 1, missing_id: 0, other_portal_id: 1}


def test_listing_stays_globally_active_when_another_search_still_matches(setup):
    repository, connection, first_search, second_search = setup
    listing = _listing(PortalKey.PISOSCOM, "shared")
    listing_id = repository.ingest(first_search, listing).listing_id
    repository.ingest(second_search, listing)

    result = repository.reconcile_portal(
        first_search, PortalKey.PISOSCOM, set(), RunStatus.SUCCESS
    )

    assert result.inactive_listing_ids == ()
    assert connection.execute(
        "SELECT active FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()[0] == 1
