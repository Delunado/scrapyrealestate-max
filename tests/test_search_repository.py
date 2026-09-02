from pathlib import Path

import pytest

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.searches import (
    SearchConflictError,
    SearchNotFoundError,
    SearchPortalRecord,
    SearchRepository,
)


@pytest.fixture
def repository(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        yield SearchRepository(connection)


def _search(name: str = "Centro") -> NormalizedSearch:
    return NormalizedSearch(
        name=name,
        transaction_type=TransactionType.BUY,
        filters=SearchFilters(min_price_euros=100_000, max_rooms=3),
    )


def test_create_get_list_and_delete_complete_search(repository):
    created = repository.create(
        _search(),
        interval_seconds=600,
        portals=(
            SearchPortalRecord(
                PortalKey.PISOSCOM,
                "https://www.pisos.com/venta/pisos-madrid/",
                {"recent_sort": True},
            ),
        ),
    )

    assert repository.get(created.id) == created
    assert repository.list() == (created,)
    assert created.schedule.interval_seconds == 600
    assert created.search.filters.max_rooms == 3
    assert created.portals[0].adapter_options == {"recent_sort": True}
    assert repository.delete(created.id) is True
    assert repository.delete(created.id) is False
    with pytest.raises(SearchNotFoundError):
        repository.get(created.id)


def test_update_enable_schedule_and_portals(repository):
    record = repository.create(_search(), interval_seconds=600)
    updated = repository.update(
        record.id,
        _search("Retiro"),
        expected_version=record.version,
    )
    disabled = repository.set_enabled(record.id, False)
    scheduled = repository.update_schedule(
        record.id,
        interval_seconds=900,
        next_run_at="2026-09-03T10:00:00Z",
    )
    portals = repository.replace_portals(
        record.id,
        (SearchPortalRecord(PortalKey.HABITACLIA, enabled=False),),
    )

    assert updated.search.name == "Retiro"
    assert disabled.enabled is False
    assert scheduled.schedule.next_run_at == "2026-09-03T10:00:00Z"
    assert portals.portals[0].portal is PortalKey.HABITACLIA
    assert portals.portals[0].enabled is False
    assert portals.version == 4
    assert repository.list(enabled=True) == ()


def test_update_rejects_stale_version(repository):
    record = repository.create(_search(), interval_seconds=600)
    repository.update(record.id, _search("Changed"), expected_version=record.version)

    with pytest.raises(SearchConflictError):
        repository.update(record.id, _search("Stale"), expected_version=record.version)


def test_create_rolls_back_when_portal_selection_is_invalid(repository):
    duplicate_portals = (
        SearchPortalRecord(PortalKey.PISOSCOM),
        SearchPortalRecord(PortalKey.PISOSCOM),
    )
    with pytest.raises(Exception):
        repository.create(_search(), interval_seconds=600, portals=duplicate_portals)

    assert repository.list() == ()
