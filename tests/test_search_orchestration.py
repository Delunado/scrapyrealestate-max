from pathlib import Path

import pytest

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.execution.contract import PortalRunResult, utc_now
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.runs import RunRepository, SearchRunStatus, TriggerKind
from scrapyrealestate.persistence.searches import SearchPortalRecord, SearchRepository
from scrapyrealestate.portals.idealista import IdealistaAdapter
from scrapyrealestate.portals.pisoscom import PisoscomAdapter
from scrapyrealestate.portals.registry import PortalRegistry
from scrapyrealestate.runtime import RuntimePaths
from scrapyrealestate.services.search_orchestration import SearchOrchestrationService


class _StubRunner:
    """Never launches a real subprocess; returns fixed items with a fresh,
    always-valid timestamp pair computed at call time (a run's attempt row
    already has a real `started_at` by the time this runs)."""

    def __init__(self, items: tuple) -> None:
        self._items = items
        self.received_requests = []

    def run(self, request):
        self.received_requests.append(request)
        return PortalRunResult(
            portal=request.portal,
            status=RunStatus.SUCCESS,
            started_at=utc_now(),
            finished_at=utc_now(),
            items=self._items,
        )


_PISOSCOM_ITEMS = (
    {
        "id": "1",
        "price": "150.000€",
        "m2": "80 m²",
        "rooms": "3",
        "town": "Madrid",
        "type": "venta",
        "title": "Piso en Madrid",
        "href": "https://www.pisos.com/comprar/piso1",
    },
    {
        "id": "2",
        "price": "500.000€",
        "town": "Madrid",
        "type": "venta",
        "title": "Piso caro en Madrid",
        "href": "https://www.pisos.com/comprar/piso2",
    },
    {
        "id": "3",
        "town": "Madrid",
        "type": "venta",
        "title": "Piso sin precio en Madrid",
        "href": "https://www.pisos.com/comprar/piso3",
    },
)


@pytest.fixture
def orchestration(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        search = NormalizedSearch(
            name="Madrid flats",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(
                location="Madrid", min_price_euros=100_000, max_price_euros=250_000
            ),
        )
        record = searches.create(
            search,
            interval_seconds=600,
            portals=(
                SearchPortalRecord(portal=PortalKey.PISOSCOM),
                SearchPortalRecord(portal=PortalKey.IDEALISTA),
                SearchPortalRecord(portal=PortalKey.FOTOCASA),
                SearchPortalRecord(portal=PortalKey.HABITACLIA, enabled=False),
            ),
        )

        registry = PortalRegistry([PisoscomAdapter(), IdealistaAdapter()])
        runner = _StubRunner(_PISOSCOM_ITEMS)
        runtime_paths = RuntimePaths(tmp_path / "data")
        service = SearchOrchestrationService(
            registry=registry, runner=runner, runs=runs, runtime_paths=runtime_paths
        )
        yield service, record, runs, runner


def test_run_search_records_every_enabled_portal_attempt(orchestration):
    service, record, runs, _runner = orchestration

    outcome = service.run_search(record, TriggerKind.MANUAL)

    portals_attempted = {a.attempt.portal for a in outcome.attempts}
    assert portals_attempted == {PortalKey.PISOSCOM, PortalKey.IDEALISTA, PortalKey.FOTOCASA}
    # The disabled portal never gets an attempt at all.
    assert PortalKey.HABITACLIA not in portals_attempted
    assert len(runs.attempts_for_run(outcome.run.id)) == 3


def test_a_portal_the_registry_does_not_know_is_unavailable_not_a_crash(orchestration):
    service, record, _runs, _runner = orchestration

    outcome = service.run_search(record, TriggerKind.MANUAL)

    fotocasa = next(a for a in outcome.attempts if a.attempt.portal is PortalKey.FOTOCASA)
    assert fotocasa.result.status is RunStatus.UNAVAILABLE
    assert fotocasa.attempt.status == "unavailable"
    assert fotocasa.listings == ()
    assert fotocasa.matched_listings == ()


def test_a_portal_that_cannot_build_a_search_url_is_unavailable_not_a_crash(orchestration):
    service, record, _runs, _runner = orchestration

    outcome = service.run_search(record, TriggerKind.MANUAL)

    idealista = next(a for a in outcome.attempts if a.attempt.portal is PortalKey.IDEALISTA)
    assert idealista.result.status is RunStatus.UNAVAILABLE
    assert "not implemented" in idealista.result.diagnostic


def test_successful_attempt_is_normalized_and_locally_filtered(orchestration):
    service, record, _runs, runner = orchestration

    outcome = service.run_search(record, TriggerKind.MANUAL)

    pisoscom = next(a for a in outcome.attempts if a.attempt.portal is PortalKey.PISOSCOM)
    assert pisoscom.result.status is RunStatus.SUCCESS
    assert len(pisoscom.listings) == 3
    # The 500.000€ listing is a definite non-match (max_price_euros=250000);
    # the listing with no price at all is kept because "unknown" must not
    # silently narrow the result set.
    matched_ids = {listing.external_id for listing in pisoscom.matched_listings}
    assert matched_ids == {"1", "3"}
    assert pisoscom.attempt.counts.returned == 3
    assert pisoscom.attempt.counts.matched == 2
    assert len(runner.received_requests) == 1
    assert "pisoscom" in str(runner.received_requests[0].output_path)


def test_mixed_success_and_failure_yields_a_partial_run(orchestration):
    service, record, _runs, _runner = orchestration

    outcome = service.run_search(record, TriggerKind.MANUAL)

    assert outcome.run.status is SearchRunStatus.PARTIAL
    assert outcome.run.counts.returned == 3
    assert outcome.run.counts.matched == 2


def test_run_with_no_enabled_portals_is_recorded_as_failed(orchestration):
    service, record, runs, _runner = orchestration

    # Disable every portal on the already-created search.
    disabled_record = _disable_all_portals(record)

    outcome = service.run_search(disabled_record, TriggerKind.MANUAL)

    assert outcome.attempts == ()
    assert outcome.run.status is SearchRunStatus.FAILED
    assert outcome.run.redacted_diagnostic == "no enabled portals"


def _disable_all_portals(record):
    from dataclasses import replace

    return replace(
        record,
        portals=tuple(replace(p, enabled=False) for p in record.portals),
    )


def test_all_portals_failing_yields_a_failed_run(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        search = NormalizedSearch(
            name="No location search",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(),  # no location -> Pisoscom cannot build a URL either
        )
        record = searches.create(
            search,
            interval_seconds=600,
            portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
        )
        registry = PortalRegistry([PisoscomAdapter()])
        runner = _StubRunner(_PISOSCOM_ITEMS)
        runtime_paths = RuntimePaths(tmp_path / "data")
        service = SearchOrchestrationService(
            registry=registry, runner=runner, runs=runs, runtime_paths=runtime_paths
        )

        outcome = service.run_search(record, TriggerKind.MANUAL)

        assert outcome.run.status is SearchRunStatus.FAILED
        assert runner.received_requests == []
