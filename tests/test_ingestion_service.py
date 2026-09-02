from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.listings import (
    ListingIdentityConflictError,
    ListingMatchRepository,
)
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import NotificationEventType
from scrapyrealestate.services.ingestion import IngestionService


@pytest.fixture
def setup(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
        ).fetchone()[0]
        yield IngestionService(connection), connection, search_id


def _listing(**changes) -> NormalizedListing:
    values = {
        "portal": PortalKey.PISOSCOM,
        "external_id": "1",
        "transaction_type": TransactionType.BUY,
        "title": "Piso céntrico",
        "price_euros": 200_000,
        "observed_at": datetime(2026, 9, 1, 10, tzinfo=timezone.utc),
    }
    values.update(changes)
    return NormalizedListing(**values)


def test_new_listing_records_price_and_raises_a_new_listing_event(setup):
    service, connection, search_id = setup

    outcome = service.ingest_attempt(
        search_id, PortalKey.PISOSCOM, (_listing(),), RunStatus.SUCCESS
    )

    assert outcome.new == 1
    assert outcome.changed == outcome.reappeared == outcome.unchanged == 0
    assert len(outcome.listing_ids) == 1
    (listing_id,) = outcome.listing_ids
    assert [event.event_type for event in outcome.events] == [
        NotificationEventType.NEW_LISTING
    ]
    price_rows = connection.execute(
        "SELECT price_euros FROM listing_price_history WHERE listing_id = ?",
        (listing_id,),
    ).fetchall()
    assert [row["price_euros"] for row in price_rows] == [200_000]


def test_price_drop_raises_a_price_drop_event_but_not_a_new_listing_event(setup):
    service, _connection, search_id = setup
    service.ingest_attempt(search_id, PortalKey.PISOSCOM, (_listing(),), RunStatus.SUCCESS)

    outcome = service.ingest_attempt(
        search_id,
        PortalKey.PISOSCOM,
        (
            _listing(
                price_euros=180_000,
                observed_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
            ),
        ),
        RunStatus.SUCCESS,
    )

    assert outcome.changed == 1
    assert [event.event_type for event in outcome.events] == [
        NotificationEventType.PRICE_DROP
    ]
    assert outcome.events[0].payload == {
        "previous_price_euros": 200_000,
        "price_euros": 180_000,
    }


def test_price_increase_raises_a_price_increase_event(setup):
    service, _connection, search_id = setup
    service.ingest_attempt(search_id, PortalKey.PISOSCOM, (_listing(),), RunStatus.SUCCESS)

    outcome = service.ingest_attempt(
        search_id,
        PortalKey.PISOSCOM,
        (
            _listing(
                price_euros=220_000,
                observed_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
            ),
        ),
        RunStatus.SUCCESS,
    )

    assert [event.event_type for event in outcome.events] == [
        NotificationEventType.PRICE_INCREASE
    ]


def test_unchanged_price_raises_no_price_event(setup):
    service, _connection, search_id = setup
    service.ingest_attempt(search_id, PortalKey.PISOSCOM, (_listing(),), RunStatus.SUCCESS)

    outcome = service.ingest_attempt(
        search_id,
        PortalKey.PISOSCOM,
        (_listing(observed_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc)),),
        RunStatus.SUCCESS,
    )

    assert outcome.unchanged == 1
    assert outcome.events == ()


def test_reappearance_raises_a_reappearance_event(setup):
    service, connection, search_id = setup
    first = service.ingest_attempt(
        search_id, PortalKey.PISOSCOM, (_listing(),), RunStatus.SUCCESS
    )
    connection.execute(
        "UPDATE search_listing_matches SET active = 0 WHERE search_id = ?", (search_id,)
    )

    outcome = service.ingest_attempt(
        search_id,
        PortalKey.PISOSCOM,
        (_listing(observed_at=datetime(2026, 9, 3, 10, tzinfo=timezone.utc)),),
        RunStatus.SUCCESS,
    )

    assert outcome.reappeared == 1
    assert outcome.listing_ids == first.listing_ids
    assert [event.event_type for event in outcome.events] == [
        NotificationEventType.REAPPEARANCE
    ]


def test_duplicate_results_within_one_attempt_upsert_once(setup):
    service, connection, search_id = setup

    outcome = service.ingest_attempt(
        search_id,
        PortalKey.PISOSCOM,
        (_listing(), _listing()),
        RunStatus.SUCCESS,
    )

    assert outcome.new == 1
    assert outcome.unchanged == 1
    assert len(set(outcome.listing_ids)) == 1
    assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 1


def test_conclusive_status_deactivates_unseen_listings_on_this_portal(setup):
    service, connection, search_id = setup
    first = service.ingest_attempt(
        search_id, PortalKey.PISOSCOM, (_listing(),), RunStatus.SUCCESS
    )
    (listing_id,) = first.listing_ids

    outcome = service.ingest_attempt(
        search_id, PortalKey.PISOSCOM, (), RunStatus.EMPTY
    )

    assert outcome.disappearance.inactive_listing_ids == (listing_id,)
    assert connection.execute(
        "SELECT active FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()[0] == 0


def test_inconclusive_status_never_deactivates_anything(setup):
    service, connection, search_id = setup
    first = service.ingest_attempt(
        search_id, PortalKey.PISOSCOM, (_listing(),), RunStatus.SUCCESS
    )
    (listing_id,) = first.listing_ids

    outcome = service.ingest_attempt(
        search_id, PortalKey.PISOSCOM, (), RunStatus.TIMEOUT
    )

    assert outcome.disappearance.reconciled is False
    assert connection.execute(
        "SELECT active FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()[0] == 1


def test_a_conflicting_listing_rolls_back_the_whole_batch(setup):
    service, connection, search_id = setup
    existing_a = ListingMatchRepository(connection).ingest(
        search_id, _listing(external_id="a", canonical_url="https://www.pisos.com/a/")
    )
    existing_b = ListingMatchRepository(connection).ingest(
        search_id, _listing(external_id="b", canonical_url="https://www.pisos.com/b/")
    )
    fresh = _listing(external_id="fresh")
    conflicting = _listing(external_id="a", canonical_url="https://www.pisos.com/b/")

    with pytest.raises(ListingIdentityConflictError):
        service.ingest_attempt(
            search_id, PortalKey.PISOSCOM, (fresh, conflicting), RunStatus.SUCCESS
        )

    # Neither the earlier item in this same batch, nor any state from the
    # two pre-existing listings, was left half-applied.
    assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 2
    assert connection.execute(
        "SELECT external_id FROM listings WHERE id = ?", (existing_a.listing_id,)
    ).fetchone()[0] == "a"
    assert connection.execute(
        "SELECT external_id FROM listings WHERE id = ?", (existing_b.listing_id,)
    ).fetchone()[0] == "b"


def test_ingest_attempt_rejects_a_listing_from_another_portal(setup):
    service, _connection, search_id = setup

    with pytest.raises(ValueError, match="does not match"):
        service.ingest_attempt(
            search_id,
            PortalKey.PISOSCOM,
            (_listing(portal=PortalKey.HABITACLIA),),
            RunStatus.SUCCESS,
        )
