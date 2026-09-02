from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.listings import (
    ListingIdentityConflictError,
    ListingMatchOutcome,
    ListingMatchRepository,
)
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


@pytest.fixture
def repository_and_connection(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            """
            INSERT INTO searches (name, transaction_type) VALUES ('Centro', 'buy')
            RETURNING id
            """
        ).fetchone()[0]
        yield ListingMatchRepository(connection), connection, search_id


def _listing(**changes) -> NormalizedListing:
    values = {
        "portal": PortalKey.PISOSCOM,
        "external_id": "123",
        "canonical_url": "https://www.pisos.com/comprar/piso-centro-123/",
        "transaction_type": TransactionType.BUY,
        "title": "Piso céntrico",
        "price_euros": 200_000,
        "observed_at": datetime(2026, 9, 1, 10, tzinfo=timezone.utc),
        "raw_source": {"price": "200.000 €"},
    }
    values.update(changes)
    return NormalizedListing(**values)


def test_ingest_returns_new_changed_and_unchanged(repository_and_connection):
    repository, connection, search_id = repository_and_connection
    first = repository.ingest(search_id, _listing())
    unchanged = repository.ingest(
        search_id,
        _listing(observed_at=datetime(2026, 9, 1, 11, tzinfo=timezone.utc)),
    )
    changed = repository.ingest(
        search_id,
        _listing(
            price_euros=190_000,
            observed_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        ),
    )

    assert first.outcome is ListingMatchOutcome.NEW
    assert first.listing_created is True
    assert unchanged.outcome is ListingMatchOutcome.UNCHANGED
    assert changed.outcome is ListingMatchOutcome.CHANGED
    assert changed.listing_id == first.listing_id
    row = repository.get(first.listing_id)
    assert row["price_euros"] == 190_000
    assert row["first_seen_at"] == "2026-09-01T10:00:00Z"
    assert row["last_seen_at"] == "2026-09-01T12:00:00Z"
    assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 1


def test_known_listing_is_new_to_another_search(repository_and_connection):
    repository, connection, first_search = repository_and_connection
    listing = repository.ingest(first_search, _listing())
    second_search = connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('Norte', 'buy') RETURNING id"
    ).fetchone()[0]

    result = repository.ingest(second_search, _listing())

    assert result.outcome is ListingMatchOutcome.NEW
    assert result.listing_created is False
    assert result.listing_id == listing.listing_id


def test_inactive_match_returns_reappeared(repository_and_connection):
    repository, connection, search_id = repository_and_connection
    first = repository.ingest(search_id, _listing())
    connection.execute(
        "UPDATE search_listing_matches SET active = 0 WHERE search_id = ?",
        (search_id,),
    )

    result = repository.ingest(
        search_id,
        _listing(observed_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc)),
    )

    assert result.outcome is ListingMatchOutcome.REAPPEARED
    assert result.listing_id == first.listing_id


def test_identity_conflict_rolls_back_without_updates(repository_and_connection):
    repository, connection, search_id = repository_and_connection
    first = repository.ingest(search_id, _listing())
    other = replace(
        _listing(),
        external_id="456",
        canonical_url="https://www.pisos.com/comprar/piso-norte-456/",
    )
    repository.ingest(search_id, other)

    with pytest.raises(ListingIdentityConflictError):
        repository.ingest(
            search_id,
            replace(_listing(), canonical_url=other.canonical_url, price_euros=1),
        )

    assert repository.get(first.listing_id)["price_euros"] == 200_000
