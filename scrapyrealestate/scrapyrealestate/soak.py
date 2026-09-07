"""Deterministic fixture soak workload intended to run inside the image."""

from __future__ import annotations

import argparse
import random
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Sequence

from scrapyrealestate.atomic_files import atomic_write_json
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.execution.contract import PortalRunResult, utc_now
from scrapyrealestate.notifiers import DeliveryResult, NotifierRegistry
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import (
    NotificationProvider,
    NotificationRepository,
)
from scrapyrealestate.persistence.runs import RunRepository, TriggerKind
from scrapyrealestate.persistence.searches import SearchPortalRecord, SearchRepository
from scrapyrealestate.portals.registry import PortalRegistry
from scrapyrealestate.portals.pisoscom import PisoscomAdapter
from scrapyrealestate.runtime import RuntimePaths, get_runtime_paths
from scrapyrealestate.services.ingestion import IngestionService
from scrapyrealestate.services.locks import SearchAlreadyRunningError
from scrapyrealestate.services.notification_delivery import DurableNotificationDispatcher
from scrapyrealestate.services.scheduler import InProcessScheduler
from scrapyrealestate.services.search_orchestration import SearchOrchestrationService
from scrapyrealestate.services.search_triggering import SearchTriggerService


class _FixtureRunner:
    def __init__(self, delay_seconds: float = 0.01) -> None:
        self.delay_seconds = delay_seconds
        self._guard = threading.Lock()
        self._active = Counter()
        self.max_active = Counter()
        self.entered = threading.Event()
        self.accepting = True

    @property
    def active_process_count(self) -> int:
        with self._guard:
            return sum(self._active.values())

    def prepare_overlap_probe(self) -> None:
        self.entered.clear()

    def run(self, request):
        search_id = int(request.output_path.name.split("-")[1])
        with self._guard:
            self._active[search_id] += 1
            self.max_active[search_id] = max(
                self.max_active[search_id], self._active[search_id]
            )
        self.entered.set()
        started = utc_now()
        try:
            time.sleep(self.delay_seconds)
            item = {
                "id": f"fixture-{search_id}",
                "title": f"Fixture home {search_id}",
                "price": f"{100_000 + search_id}.000 EUR",
                "m2": "80 m2",
                "rooms": "3",
                "town": "Madrid",
                "type": "buy",
                "href": f"https://www.pisos.com/comprar/fixture-{search_id}/",
                "site": "pisoscom",
            }
            return PortalRunResult(
                portal=request.portal,
                status=RunStatus.SUCCESS,
                started_at=started,
                finished_at=utc_now(),
                items=(item,),
            )
        finally:
            with self._guard:
                self._active[search_id] -= 1

    def stop_accepting(self) -> None:
        self.accepting = False

    def shutdown(self, _grace_seconds: float = 0) -> bool:
        self.stop_accepting()
        return self.active_process_count == 0


class _SuccessNotifier:
    def send(self, event):
        return DeliveryResult.delivered(f"soak-{event.id}")


class _Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def run_soak(paths: RuntimePaths, *, cycles: int = 20) -> dict[str, object]:
    if cycles < 3:
        raise ValueError("cycles must be at least 3")
    paths.ensure_data_dir()
    database = Database(paths.database_file)
    runner = _FixtureRunner()
    with database.connection(check_same_thread=False) as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        searches = SearchRepository(connection)
        notifications = NotificationRepository(connection)
        channel = notifications.create_channel(
            "Soak recorder",
            NotificationProvider.WEBHOOK,
            config={"endpoint_url": "https://example.invalid/soak"},
        )
        records = []
        for number, interval in enumerate((300, 600, 900), start=1):
            record = searches.create(
                NormalizedSearch(
                    name=f"Fixture schedule {number}",
                    transaction_type=TransactionType.BUY,
                    filters=SearchFilters(location="Madrid"),
                ),
                interval_seconds=interval,
                portals=(SearchPortalRecord(portal=PortalKey.PISOSCOM),),
            )
            notifications.assign_channel(record.id, channel.id)
            records.append(record)

        notifier_registry = NotifierRegistry()
        notifier_registry.register(
            NotificationProvider.WEBHOOK, lambda _channel: _SuccessNotifier()
        )
        orchestration = SearchOrchestrationService(
            registry=PortalRegistry([PisoscomAdapter()]),
            runner=runner,
            runs=RunRepository(connection),
            ingestion=IngestionService(connection),
            runtime_paths=paths,
            notification_delivery=DurableNotificationDispatcher(
                notifications, notifier_registry
            ),
            random_source=random.Random(0),
        )
        trigger = SearchTriggerService(searches, orchestration)
        clock = _Clock(datetime(2026, 9, 7, 10, tzinfo=timezone.utc))
        scheduler = InProcessScheduler(searches, trigger, clock=clock)
        scheduler.refresh(reset=True)
        for _ in range(cycles):
            clock.current += timedelta(seconds=300)
            scheduler.run_due()

        scheduled_counts = {
            row["search_id"]: row["total"]
            for row in connection.execute(
                """
                SELECT search_id, count(*) AS total FROM search_runs
                WHERE trigger_kind = 'scheduled' GROUP BY search_id
                """
            ).fetchall()
        }

        overlap_errors = []
        runner.prepare_overlap_probe()
        worker = threading.Thread(
            target=lambda: _capture_run(trigger, records[0].id, overlap_errors),
            name="soak-overlap-owner",
        )
        worker.start()
        if not runner.entered.wait(timeout=2):
            raise RuntimeError("overlap probe did not enter the fixture runner")
        conflicts = 0
        try:
            trigger.run_search(records[0].id, TriggerKind.MANUAL)
        except SearchAlreadyRunningError:
            conflicts += 1
        worker.join(timeout=2)
        if worker.is_alive() or overlap_errors:
            raise RuntimeError("overlap probe did not finish cleanly")

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        event_count = connection.execute(
            "SELECT count(*) FROM notification_events"
        ).fetchone()[0]
        delivery_count = connection.execute(
            "SELECT count(*) FROM notification_delivery_attempts WHERE status = 'succeeded'"
        ).fetchone()[0]
        duplicate_delivery_pairs = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT event_id, channel_id FROM notification_delivery_attempts
                WHERE status = 'succeeded' GROUP BY event_id, channel_id
                HAVING count(*) > 1
            )
            """
        ).fetchone()[0]
        report = {
            "schema_version": "1.0",
            "cycles": cycles,
            "search_count": len(records),
            "scheduled_runs": {
                str(record.id): scheduled_counts.get(record.id, 0) for record in records
            },
            "same_search_conflicts": conflicts,
            "max_overlap_per_search": {
                str(search_id): maximum
                for search_id, maximum in sorted(runner.max_active.items())
            },
            "database_integrity": integrity,
            "notification_event_count": event_count,
            "succeeded_delivery_count": delivery_count,
            "duplicate_delivery_pairs": duplicate_delivery_pairs,
            "active_fixture_attempts": runner.active_process_count,
        }
    atomic_write_json(paths.data_dir / "soak-report.json", report)
    return report


def _capture_run(trigger, search_id: int, errors: list[BaseException]) -> None:
    try:
        trigger.run_search(search_id, TriggerKind.MANUAL)
    except BaseException as error:
        errors.append(error)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=20)
    arguments = parser.parse_args(argv)
    report = run_soak(get_runtime_paths(), cycles=arguments.cycles)
    print(pathsafe_summary(report))
    return 0


def pathsafe_summary(report: dict[str, object]) -> str:
    return (
        f"fixture soak passed: {report['cycles']} cycles, "
        f"{report['search_count']} searches, integrity={report['database_integrity']}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
