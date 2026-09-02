import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import TransactionType
from scrapyrealestate.persistence.runs import TriggerKind
from scrapyrealestate.persistence.searches import SearchRecord, SearchScheduleRecord
from scrapyrealestate.services.scheduler import InProcessScheduler


NOW = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)


def _record(
    search_id: int,
    *,
    interval_seconds: int = 600,
    enabled: bool = True,
) -> SearchRecord:
    return SearchRecord(
        id=search_id,
        search=NormalizedSearch(
            name=f"Search {search_id}",
            transaction_type=TransactionType.RENT,
            filters=SearchFilters(),
        ),
        enabled=enabled,
        version=1,
        schedule=SearchScheduleRecord(interval_seconds=interval_seconds),
        portals=(),
        created_at="2026-09-02T09:00:00Z",
        updated_at="2026-09-02T09:00:00Z",
    )


class FakeSearchSource:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = 0
        self.loaded = threading.Event()

    def list(self, *, enabled=None):
        self.calls += 1
        self.loaded.set()
        if enabled is None:
            return self.records
        return tuple(record for record in self.records if record.enabled is enabled)


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def run_search(self, search_record, trigger):
        self.calls.append((search_record, trigger))


def test_refresh_loads_enabled_searches_and_computes_utc_deadlines():
    source = FakeSearchSource((_record(1), _record(2, enabled=False)))
    scheduler = InProcessScheduler(source, RecordingExecutor(), clock=lambda: NOW)

    next_runs = scheduler.refresh()

    assert dict(next_runs) == {1: NOW + timedelta(seconds=600)}
    assert next_runs[1].tzinfo is timezone.utc


def test_due_search_uses_scheduled_orchestration_trigger_and_is_rescheduled():
    current = [NOW]
    executor = RecordingExecutor()
    scheduler = InProcessScheduler(
        FakeSearchSource((_record(1),)),
        executor,
        clock=lambda: current[0],
    )
    scheduler.refresh()
    current[0] += timedelta(seconds=600)

    assert scheduler.run_due() == (1,)
    assert executor.calls[0][0].id == 1
    assert executor.calls[0][1] is TriggerKind.SCHEDULED
    assert scheduler.next_run_times[1] == current[0] + timedelta(seconds=600)


def test_schedule_change_wakes_worker_without_periodic_polling():
    source = FakeSearchSource((_record(1, interval_seconds=3600),))
    scheduler = InProcessScheduler(source, RecordingExecutor(), clock=lambda: NOW)
    scheduler.start()
    assert source.loaded.wait(1)

    assert source.calls == 1
    source.records = (replace(_record(1), version=2), _record(2, interval_seconds=900))
    source.loaded.clear()
    scheduler.notify_schedule_changed()

    assert source.loaded.wait(1)
    assert scheduler.stop(timeout=1)
    assert source.calls == 2
    assert scheduler.next_run_times == {
        1: NOW + timedelta(seconds=600),
        2: NOW + timedelta(seconds=900),
    }


def test_scheduler_rejects_a_naive_clock():
    scheduler = InProcessScheduler(
        FakeSearchSource((_record(1),)),
        RecordingExecutor(),
        clock=lambda: datetime(2026, 9, 2, 10, 0),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        scheduler.refresh()
