import threading
from dataclasses import replace
from pathlib import Path

import pytest

from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.execution.contract import PortalRunResult, utc_now
from scrapyrealestate.notifiers import DeliveryResult, NotifierRegistry
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.listings import ListingMatchRepository
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import (
    DeliveryStatus,
    NotificationEventType,
    NotificationProvider,
    NotificationRepository,
)
from scrapyrealestate.persistence.runs import RunRepository, SearchRunStatus, TriggerKind
from scrapyrealestate.persistence.searches import SearchPortalRecord, SearchRepository
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
)
from scrapyrealestate.portals.idealista import IdealistaAdapter
from scrapyrealestate.portals.pisoscom import PisoscomAdapter
from scrapyrealestate.portals.registry import PortalRegistry
from scrapyrealestate.runtime import RuntimePaths
from scrapyrealestate.services.ingestion import IngestionService
from scrapyrealestate.services.locks import SearchAlreadyRunningError, SearchRunLock
from scrapyrealestate.services.notification_delivery import DurableNotificationDispatcher
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


class _RecordingNotifier:
    def __init__(self, calls, result):
        self._calls = calls
        self._result = result

    def send(self, event):
        self._calls.append(event)
        return self._result


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


def _make_succeeding_adapter(portal: PortalKey, domain: str) -> BasePortalAdapter:
    """An adapter that always builds a request and normalizes trivially,
    for tests that care about orchestration mechanics (order, delay,
    ingestion) rather than any one portal's parsing quirks."""

    class _Adapter(BasePortalAdapter):
        _METADATA = PortalMetadata(
            key=portal,
            display_name=portal.value,
            domains=frozenset({domain}),
            spider_name=portal.value,
            transaction_types=frozenset({TransactionType.BUY}),
            transport=PortalTransport.HTTP,
            capabilities=ALL_LOCAL_CAPABILITIES,
        )

        def _transaction_type(self, raw_url):
            return TransactionType.BUY

        def _apply_recent_sort(self, raw_url):
            return raw_url

        def _build_search_url(self, transaction_type, location_slug):
            return f"https://{domain}/search/{location_slug}"

        def normalize_result(self, item):
            return NormalizedListing(
                portal=portal,
                external_id=item["id"],
                transaction_type=TransactionType.BUY,
                title=item.get("title", f"Listing {item['id']}"),
                price_euros=item.get("price_euros"),
                rooms=item.get("rooms"),
            )

    return _Adapter()


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
        ingestion = IngestionService(connection)
        service = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=ingestion,
            runtime_paths=runtime_paths,
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
        ingestion = IngestionService(connection)
        service = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=ingestion,
            runtime_paths=runtime_paths,
        )

        outcome = service.run_search(record, TriggerKind.MANUAL)

        assert outcome.run.status is SearchRunStatus.FAILED
        assert runner.received_requests == []


def test_matched_listings_are_ingested_transactionally(orchestration):
    service, record, _runs, _runner = orchestration

    outcome = service.run_search(record, TriggerKind.MANUAL)

    pisoscom = next(a for a in outcome.attempts if a.attempt.portal is PortalKey.PISOSCOM)
    assert pisoscom.ingestion is not None
    assert pisoscom.ingestion.new == 2
    assert pisoscom.attempt.counts.new == 2
    assert outcome.run.counts.new == 2


def test_unknown_filter_values_are_matched_and_still_ingested(tmp_path: Path):
    # A listing missing the field a filter constrains ("rooms" here) must be
    # treated as unknown, not excluded - and that "kept" decision has to
    # actually reach persistence, not just the in-memory filter step.
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        search = NormalizedSearch(
            name="Rooms search",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(location="Madrid", min_rooms=2),
        )
        record = searches.create(
            search,
            interval_seconds=600,
            portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
        )
        adapter = _make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")
        registry = PortalRegistry([adapter])
        runner = _StubRunner(({"id": "no-rooms"},))
        service = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=IngestionService(connection),
            runtime_paths=RuntimePaths(tmp_path / "data"),
        )

        outcome = service.run_search(record, TriggerKind.MANUAL)

        pisoscom = outcome.attempts[0]
        assert {listing.external_id for listing in pisoscom.matched_listings} == {"no-rooms"}
        assert pisoscom.ingestion.new == 1
        assert connection.execute(
            "SELECT external_id FROM listings"
        ).fetchone()[0] == "no-rooms"


def test_duplicate_portal_results_upsert_into_a_single_listing(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        search = NormalizedSearch(
            name="Duplicates search",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(location="Madrid"),
        )
        record = searches.create(
            search,
            interval_seconds=600,
            portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
        )
        adapter = _make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")
        registry = PortalRegistry([adapter])
        runner = _StubRunner(({"id": "dup"}, {"id": "dup"}))
        service = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=IngestionService(connection),
            runtime_paths=RuntimePaths(tmp_path / "data"),
        )

        outcome = service.run_search(record, TriggerKind.MANUAL)

        pisoscom = outcome.attempts[0]
        assert len(pisoscom.matched_listings) == 2
        assert pisoscom.ingestion.new == 1
        assert pisoscom.ingestion.unchanged == 1
        assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 1


def test_ingestion_failure_is_recorded_and_never_crashes_the_run(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        search = NormalizedSearch(
            name="Madrid flats",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(location="Madrid"),
        )
        record = searches.create(
            search,
            interval_seconds=600,
            portals=(
                SearchPortalRecord(portal=PortalKey.PISOSCOM),
                SearchPortalRecord(portal=PortalKey.IDEALISTA),
            ),
        )

        # Two pre-existing listings whose external ID and canonical URL,
        # taken together, will collide with the incoming item below.
        listings = ListingMatchRepository(connection)
        listings.ingest(
            record.id,
            NormalizedListing(
                portal=PortalKey.PISOSCOM,
                external_id="1",
                transaction_type=TransactionType.BUY,
                title="Existing by external id",
            ),
        )
        listings.ingest(
            record.id,
            NormalizedListing(
                portal=PortalKey.PISOSCOM,
                external_id="other",
                canonical_url="https://www.pisos.com/comprar/piso1",
                transaction_type=TransactionType.BUY,
                title="Existing by canonical url",
            ),
        )

        registry = PortalRegistry([PisoscomAdapter(), IdealistaAdapter()])
        runner = _StubRunner(_PISOSCOM_ITEMS)
        service = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=IngestionService(connection),
            runtime_paths=RuntimePaths(tmp_path / "data"),
        )

        outcome = service.run_search(record, TriggerKind.MANUAL)

        pisoscom = next(a for a in outcome.attempts if a.attempt.portal is PortalKey.PISOSCOM)
        assert pisoscom.ingestion is None
        assert pisoscom.attempt.error_category == "ingestion_error"
        # The fetch itself still succeeded - only ingestion failed.
        assert pisoscom.result.status is RunStatus.SUCCESS

        idealista = next(a for a in outcome.attempts if a.attempt.portal is PortalKey.IDEALISTA)
        assert idealista.result.status is RunStatus.UNAVAILABLE

        # Nothing from the conflicting batch (nor from the two pre-existing
        # rows) was left half-applied.
        assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 2


def test_portal_order_is_randomized_via_the_injected_source(tmp_path: Path):
    class _ReverseOrder:
        def shuffle(self, items):
            items.reverse()

    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        search = NormalizedSearch(
            name="Ordering search",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(location="Madrid"),
        )
        record = searches.create(
            search,
            interval_seconds=600,
            portals=(
                SearchPortalRecord(portal=PortalKey.PISOSCOM),
                SearchPortalRecord(portal=PortalKey.HABITACLIA),
                SearchPortalRecord(portal=PortalKey.FOTOCASA),
            ),
        )
        registry = PortalRegistry(
            [
                _make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com"),
                _make_succeeding_adapter(PortalKey.HABITACLIA, "habitaclia.com"),
                _make_succeeding_adapter(PortalKey.FOTOCASA, "fotocasa.es"),
            ]
        )
        runner = _StubRunner(())
        service = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=IngestionService(connection),
            runtime_paths=RuntimePaths(tmp_path / "data"),
            random_source=_ReverseOrder(),
        )

        service.run_search(record, TriggerKind.MANUAL)

        # SearchRecord.portals reads back ordered by portal key (fotocasa,
        # habitaclia, pisoscom); the injected source reverses whatever list
        # it is handed, so attempts run in exactly the opposite order.
        assert [request.portal for request in runner.received_requests] == [
            PortalKey.PISOSCOM,
            PortalKey.HABITACLIA,
            PortalKey.FOTOCASA,
        ]


def test_inter_portal_delay_runs_between_attempts_not_before_the_first(tmp_path: Path):
    sleep_calls: list[float] = []

    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        search = NormalizedSearch(
            name="Delay search",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(location="Madrid"),
        )
        record = searches.create(
            search,
            interval_seconds=600,
            portals=(
                SearchPortalRecord(portal=PortalKey.PISOSCOM),
                SearchPortalRecord(portal=PortalKey.HABITACLIA),
                SearchPortalRecord(portal=PortalKey.FOTOCASA),
            ),
        )
        registry = PortalRegistry(
            [
                _make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com"),
                _make_succeeding_adapter(PortalKey.HABITACLIA, "habitaclia.com"),
                _make_succeeding_adapter(PortalKey.FOTOCASA, "fotocasa.es"),
            ]
        )
        runner = _StubRunner(())
        service = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=IngestionService(connection),
            runtime_paths=RuntimePaths(tmp_path / "data"),
            inter_portal_delay_seconds=1.5,
            sleep=sleep_calls.append,
        )

        service.run_search(record, TriggerKind.MANUAL)

        assert sleep_calls == [1.5, 1.5]


def test_negative_inter_portal_delay_is_rejected(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        with pytest.raises(ValueError, match="cannot be negative"):
            SearchOrchestrationService(
                registry=PortalRegistry([]),
                runner=_StubRunner(()),
                runs=RunRepository(connection),
                ingestion=IngestionService(connection),
                runtime_paths=RuntimePaths(tmp_path / "data"),
                inter_portal_delay_seconds=-1,
            )


def test_orchestration_routes_persisted_events_once_through_durable_delivery(
    tmp_path: Path,
):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        record = SearchRepository(connection).create(
            NormalizedSearch(
                name="Notifications",
                transaction_type=TransactionType.BUY,
                filters=SearchFilters(location="Madrid"),
            ),
            interval_seconds=600,
            portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
        )
        notifications = NotificationRepository(connection)
        channel = notifications.create_channel(
            "Telegram",
            NotificationProvider.TELEGRAM,
            config={"chat_id": "123"},
            secret_config={"bot_token": "user-token"},
        )
        notifications.assign_channel(record.id, channel.id)
        delivered = []
        notifier_registry = NotifierRegistry()
        notifier_registry.register(
            NotificationProvider.TELEGRAM,
            lambda configured: _RecordingNotifier(
                delivered, DeliveryResult.delivered("telegram-1")
            ),
        )
        service = SearchOrchestrationService(
            registry=PortalRegistry(
                [_make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")]
            ),
            runner=_StubRunner(
                ({"id": "1", "title": "Piso", "price_euros": 190_000},)
            ),
            runs=RunRepository(connection),
            ingestion=IngestionService(connection),
            runtime_paths=RuntimePaths(tmp_path / "data"),
            notification_delivery=DurableNotificationDispatcher(
                notifications, notifier_registry
            ),
        )

        first = service.run_search(record, TriggerKind.MANUAL)
        second = service.run_search(record, TriggerKind.MANUAL)

        assert len(delivered) == 1
        assert delivered[0].event_type is NotificationEventType.NEW_LISTING
        assert len(first.attempts[0].notification_deliveries) == 1
        assert first.attempts[0].notification_error is None
        assert second.attempts[0].notification_deliveries == ()
        row = connection.execute(
            "SELECT * FROM notification_delivery_attempts WHERE channel_id = ?",
            (channel.id,),
        ).fetchone()
        assert row["status"] == DeliveryStatus.SUCCEEDED.value
        assert row["provider_message_id"] == "telegram-1"


def test_notification_failure_is_durable_but_does_not_fail_search_run(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        record = SearchRepository(connection).create(
            NormalizedSearch(
                name="Notifications",
                transaction_type=TransactionType.BUY,
                filters=SearchFilters(location="Madrid"),
            ),
            interval_seconds=600,
            portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
        )
        notifications = NotificationRepository(connection)
        channel = notifications.create_channel(
            "Webhook",
            NotificationProvider.WEBHOOK,
            config={"endpoint_url": "https://example.com/hook"},
        )
        notifications.assign_channel(record.id, channel.id)
        notifier_registry = NotifierRegistry()
        notifier_registry.register(
            NotificationProvider.WEBHOOK,
            lambda configured: _RecordingNotifier(
                [], DeliveryResult.failed("timeout", "webhook request timed out")
            ),
        )
        service = SearchOrchestrationService(
            registry=PortalRegistry(
                [_make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")]
            ),
            runner=_StubRunner(({"id": "1", "title": "Piso"},)),
            runs=RunRepository(connection),
            ingestion=IngestionService(connection),
            runtime_paths=RuntimePaths(tmp_path / "data"),
            notification_delivery=DurableNotificationDispatcher(
                notifications, notifier_registry
            ),
        )

        outcome = service.run_search(record, TriggerKind.MANUAL)

        assert outcome.run.status is SearchRunStatus.SUCCESS
        assert outcome.attempts[0].attempt.status == RunStatus.SUCCESS.value
        delivery = outcome.attempts[0].notification_deliveries[0]
        assert delivery.completion.attempt.status is DeliveryStatus.FAILED
        assert delivery.completion.retry is not None


class _BlockingRunner:
    """Blocks inside ``run`` until released, to hold a run "in flight" for a
    controlled window while a second call is attempted from another thread.
    Sqlite connections are not shared across threads (each side below opens
    its own connection to the same file), so only this runner needs to be
    shared.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self, request):
        self.entered.set()
        self.release.wait(timeout=5)
        return PortalRunResult(
            portal=request.portal,
            status=RunStatus.SUCCESS,
            started_at=utc_now(),
            finished_at=utc_now(),
            items=(),
        )


def test_a_second_run_of_the_same_search_while_one_is_in_progress_is_rejected(
    tmp_path: Path,
):
    db_path = tmp_path / "test.sqlite3"
    database = Database(db_path)
    with database.connection() as setup_connection:
        MigrationRunner(MIGRATIONS).migrate(setup_connection)
        search = NormalizedSearch(
            name="Locking search",
            transaction_type=TransactionType.BUY,
            filters=SearchFilters(location="Madrid"),
        )
        record = SearchRepository(setup_connection).create(
            search,
            interval_seconds=600,
            portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
        )

    # Shared across threads: pure in-process state, safe unlike a
    # sqlite3.Connection. Each side below still opens its own connection.
    locks = SearchRunLock()
    runner = _BlockingRunner()
    first_run_error: list[BaseException] = []

    def run_first() -> None:
        with database.connection() as connection:
            registry = PortalRegistry(
                [_make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")]
            )
            service = SearchOrchestrationService(
                registry=registry,
                runner=runner,
                runs=RunRepository(connection),
                ingestion=IngestionService(connection),
                runtime_paths=RuntimePaths(tmp_path / "data"),
                locks=locks,
            )
            try:
                service.run_search(record, TriggerKind.MANUAL)
            except BaseException as error:  # noqa: BLE001 - captured, not swallowed
                first_run_error.append(error)

    worker = threading.Thread(target=run_first)
    worker.start()
    try:
        assert runner.entered.wait(timeout=5)
        with database.connection() as connection:
            registry = PortalRegistry(
                [_make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")]
            )
            second_service = SearchOrchestrationService(
                registry=registry,
                runner=_StubRunner(()),
                runs=RunRepository(connection),
                ingestion=IngestionService(connection),
                runtime_paths=RuntimePaths(tmp_path / "data"),
                locks=locks,
            )
            with pytest.raises(SearchAlreadyRunningError):
                second_service.run_search(record, TriggerKind.MANUAL)
    finally:
        runner.release.set()
        worker.join(timeout=5)

    assert first_run_error == []
    with database.connection() as connection:
        # Exactly one run row exists: the rejected attempt never created one.
        assert connection.execute("SELECT count(*) FROM search_runs").fetchone()[0] == 1


def test_independent_searches_run_concurrently_without_lock_contention(tmp_path: Path):
    db_path = tmp_path / "test.sqlite3"
    database = Database(db_path)
    with database.connection() as setup_connection:
        MigrationRunner(MIGRATIONS).migrate(setup_connection)
        searches = SearchRepository(setup_connection)

        def make_record(name: str):
            search = NormalizedSearch(
                name=name,
                transaction_type=TransactionType.BUY,
                filters=SearchFilters(location="Madrid"),
            )
            return searches.create(
                search,
                interval_seconds=600,
                portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
            )

        record_one = make_record("One")
        record_two = make_record("Two")

    locks = SearchRunLock()
    runner_one = _BlockingRunner()

    def run_one() -> None:
        with database.connection() as connection:
            registry = PortalRegistry(
                [_make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")]
            )
            service = SearchOrchestrationService(
                registry=registry,
                runner=runner_one,
                runs=RunRepository(connection),
                ingestion=IngestionService(connection),
                runtime_paths=RuntimePaths(tmp_path / "data"),
                locks=locks,
            )
            service.run_search(record_one, TriggerKind.MANUAL)

    worker = threading.Thread(target=run_one)
    worker.start()
    try:
        assert runner_one.entered.wait(timeout=5)
        with database.connection() as connection:
            registry = PortalRegistry(
                [_make_succeeding_adapter(PortalKey.PISOSCOM, "pisos.com")]
            )
            service_two = SearchOrchestrationService(
                registry=registry,
                runner=_StubRunner(()),
                runs=RunRepository(connection),
                ingestion=IngestionService(connection),
                runtime_paths=RuntimePaths(tmp_path / "data"),
                locks=locks,
            )
            # A different search is not blocked by search one's in-flight run.
            outcome_two = service_two.run_search(record_two, TriggerKind.MANUAL)
            assert outcome_two.run.status is SearchRunStatus.SUCCESS
    finally:
        runner_one.release.set()
        worker.join(timeout=5)
