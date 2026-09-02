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

    def update_scheduler_state(
        self, search_id, *, next_run_at, last_scheduled_at
    ):
        updated = None
        records = []
        for record in self.records:
            if record.id == search_id:
                updated = replace(
                    record,
                    schedule=replace(
                        record.schedule,
                        next_run_at=next_run_at.isoformat().replace("+00:00", "Z"),
                        last_scheduled_at=(
                            last_scheduled_at.isoformat().replace("+00:00", "Z")
                            if last_scheduled_at is not None
                            else None
                        ),
                    ),
                )
                records.append(updated)
            else:
                records.append(record)
        if updated is None:
            raise LookupError(search_id)
        self.records = tuple(records)
        return updated


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def run_search(self, search_id, trigger):
        self.calls.append((search_id, trigger))


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
    assert executor.calls[0][0] == 1
    assert executor.calls[0][1] is TriggerKind.SCHEDULED
    assert scheduler.next_run_times[1] == current[0] + timedelta(seconds=600)


def test_restart_preserves_a_persisted_future_deadline():
    persisted = (NOW + timedelta(minutes=3)).isoformat().replace("+00:00", "Z")
    record = replace(
        _record(1),
        schedule=SearchScheduleRecord(
            interval_seconds=600,
            next_run_at=persisted,
        ),
    )
    source = FakeSearchSource((record,))

    first = InProcessScheduler(source, RecordingExecutor(), clock=lambda: NOW)
    second = InProcessScheduler(source, RecordingExecutor(), clock=lambda: NOW)

    assert first.refresh()[1] == NOW + timedelta(minutes=3)
    assert second.refresh()[1] == NOW + timedelta(minutes=3)


def test_missed_deadline_runs_once_and_persists_recovery_state():
    missed = NOW - timedelta(minutes=5)
    record = replace(
        _record(1),
        schedule=SearchScheduleRecord(
            interval_seconds=600,
            next_run_at=missed.isoformat().replace("+00:00", "Z"),
        ),
    )
    source = FakeSearchSource((record,))
    executor = RecordingExecutor()
    scheduler = InProcessScheduler(source, executor, clock=lambda: NOW)

    scheduler.refresh()

    assert scheduler.run_due() == (1,)
    assert scheduler.run_due() == ()
    assert len(executor.calls) == 1
    assert source.records[0].schedule.last_scheduled_at == missed.isoformat().replace(
        "+00:00", "Z"
    )
    assert source.records[0].schedule.next_run_at == (
        NOW + timedelta(minutes=10)
    ).isoformat().replace("+00:00", "Z")


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
