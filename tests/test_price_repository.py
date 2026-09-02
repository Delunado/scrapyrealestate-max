from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.prices import (
    PriceChange,
    PriceHistoryRepository,
    PriceObservationConflictError,
)


@pytest.fixture
def repository(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        listing_id = connection.execute(
            """
            INSERT INTO listings (
                portal_key, external_id, transaction_type, title,
                first_seen_at, last_seen_at
            ) VALUES ('pisoscom', '123', 'buy', 'Piso',
                      '2026-09-01T10:00:00Z', '2026-09-01T10:00:00Z')
            RETURNING id
            """
        ).fetchone()[0]
        yield PriceHistoryRepository(connection), listing_id


def _time(hour: int) -> datetime:
    return datetime(2026, 9, 1, hour, tzinfo=timezone.utc)


def test_detects_initial_drop_increase_and_unchanged(repository):
    prices, listing_id = repository

    assert prices.record(listing_id, 200_000, _time(10)).change is PriceChange.INITIAL
    assert prices.record(listing_id, 190_000, _time(11)).change is PriceChange.DROP
    assert prices.record(listing_id, 195_000, _time(12)).change is PriceChange.INCREASE
    unchanged = prices.record(listing_id, 195_000, _time(13))

    assert unchanged.change is PriceChange.UNCHANGED
    assert unchanged.previous_price_euros == 195_000
    assert len(prices.list_for_listing(listing_id)) == 4


def test_same_observation_is_idempotent(repository):
    prices, listing_id = repository
    first = prices.record(listing_id, 200_000, _time(10))
    repeated = prices.record(listing_id, 200_000, _time(10))

    assert first.recorded is True
    assert repeated.recorded is False
    assert repeated.change is PriceChange.INITIAL
    assert len(prices.list_for_listing(listing_id)) == 1


def test_same_time_with_different_price_is_rejected(repository):
    prices, listing_id = repository
    prices.record(listing_id, 200_000, _time(10))

    with pytest.raises(PriceObservationConflictError):
        prices.record(listing_id, 190_000, _time(10))


def test_out_of_order_observation_compares_previous_chronological_price(repository):
    prices, listing_id = repository
    prices.record(listing_id, 200_000, _time(10))
    prices.record(listing_id, 180_000, _time(12))

    result = prices.record(listing_id, 190_000, _time(11))

    assert result.change is PriceChange.DROP
    assert result.previous_price_euros == 200_000
