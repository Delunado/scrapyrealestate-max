from pathlib import Path

import pytest

from scrapyrealestate.domain.notification import NotificationEventType
from scrapyrealestate.domain.values import PortalKey
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.listings import ListingQueryRepository
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


@pytest.fixture
def listing_queries(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        first_search = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('Centro', 'buy') RETURNING id"
        ).fetchone()[0]
        second_search = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('Norte', 'rent') RETURNING id"
        ).fetchone()[0]
        first_listing = _insert_listing(
            connection, "pisoscom", "1", "Primero", "2026-09-03T12:00:00Z", True
        )
        second_listing = _insert_listing(
            connection, "fotocasa", "2", "Segundo", "2026-09-03T11:00:00Z", False
        )
        connection.executemany(
            "INSERT INTO search_listing_matches (search_id, listing_id, first_seen_at, last_seen_at) VALUES (?, ?, '2026-09-03T10:00:00Z', '2026-09-03T12:00:00Z')",
            ((first_search, first_listing), (second_search, second_listing)),
        )
        connection.execute(
            """
            INSERT INTO notification_events (
                search_id, listing_id, event_type, deduplication_key, occurred_at
            ) VALUES (?, ?, 'price_drop', 'drop:2', '2026-09-03T11:00:00Z')
            """,
            (second_search, second_listing),
        )
        yield ListingQueryRepository(connection), first_search, second_search


def test_recent_listings_are_paginated_and_ordered(listing_queries):
    repository, _first_search, _second_search = listing_queries

    first_page = repository.recent(page=1, per_page=1)
    second_page = repository.recent(page=2, per_page=1)

    assert first_page.total == 2
    assert first_page.pages == 2
    assert first_page.has_next is True
    assert [item.title for item in first_page.items] == ["Primero"]
    assert [item.title for item in second_page.items] == ["Segundo"]


def test_recent_listings_combine_search_portal_event_and_state_filters(listing_queries):
    repository, first_search, second_search = listing_queries

    assert repository.recent(search_id=first_search).items[0].title == "Primero"
    filtered = repository.recent(
        search_id=second_search,
        portal=PortalKey.FOTOCASA,
        event_type=NotificationEventType.PRICE_DROP,
        active=False,
    )

    assert [item.title for item in filtered.items] == ["Segundo"]
    assert repository.recent(active=True, event_type=NotificationEventType.PRICE_DROP).total == 0


def _insert_listing(connection, portal, external_id, title, last_seen_at, active):
    return connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, transaction_type, title, first_seen_at,
            last_seen_at, active
        ) VALUES (?, ?, 'buy', ?, '2026-09-03T10:00:00Z', ?, ?) RETURNING id
        """,
        (portal, external_id, title, last_seen_at, int(active)),
    ).fetchone()[0]
