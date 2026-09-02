import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import TransactionType
from scrapyrealestate.persistence.runs import TriggerKind
from scrapyrealestate.persistence.searches import SearchRecord, SearchScheduleRecord
from scrapyrealestate.services.locks import SearchAlreadyRunningError
from scrapyrealestate.services.scheduler import InProcessScheduler
from scrapyrealestate.services.search_triggering import SearchTriggerService


NOW = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)


class ControllableClock:
    def __init__(self, current=NOW):
        self._current = current
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self._current

    def advance(self, **kwargs):
        with self._lock:
            self._current += timedelta(**kwargs)
            return self._current


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

    def get(self, search_id):
        return next(record for record in self.records if record.id == search_id)


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
    clock = ControllableClock()
    executor = RecordingExecutor()
    scheduler = InProcessScheduler(
        FakeSearchSource((_record(1),)),
        executor,
        clock=clock,
    )
    scheduler.refresh()
    current = clock.advance(seconds=600)

    assert scheduler.run_due() == (1,)
    assert executor.calls[0][0] == 1
    assert executor.calls[0][1] is TriggerKind.SCHEDULED
    assert scheduler.next_run_times[1] == current + timedelta(seconds=600)


def test_independent_intervals_become_due_on_their_own_deadlines():
    clock = ControllableClock()
    executor = RecordingExecutor()
    scheduler = InProcessScheduler(
        FakeSearchSource(
            (_record(1, interval_seconds=300), _record(2, interval_seconds=900))
        ),
        executor,
        clock=clock,
    )
    scheduler.refresh()

    clock.advance(minutes=5)
    assert scheduler.run_due() == (1,)
    assert executor.calls == [(1, TriggerKind.SCHEDULED)]

    clock.advance(minutes=10)
    assert scheduler.run_due() == (1, 2)
    assert executor.calls[-2:] == [
        (1, TriggerKind.SCHEDULED),
        (2, TriggerKind.SCHEDULED),
    ]


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


def test_search_disabled_after_refresh_is_not_dispatched():
    clock = ControllableClock()
    source = FakeSearchSource((_record(1),))
    orchestration = RecordingExecutor()
    trigger = SearchTriggerService(source, orchestration)
    scheduler = InProcessScheduler(source, trigger, clock=clock)
    scheduler.refresh()
    source.records = (replace(source.records[0], enabled=False),)
    clock.advance(minutes=10)

    assert scheduler.run_due() == (1,)
    assert orchestration.calls == []


class SelectivelyFailingExecutor(RecordingExecutor):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def run_search(self, search_id, trigger):
        super().run_search(search_id, trigger)
        failure = self.failures.get(search_id)
        if failure is not None:
            raise failure


def _all_due_source():
    due = (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    return FakeSearchSource(
        tuple(
            replace(
                _record(search_id),
                schedule=SearchScheduleRecord(600, next_run_at=due),
            )
            for search_id in (1, 2)
        )
    )


def test_lock_contention_does_not_prevent_an_independent_search_run(caplog):
    executor = SelectivelyFailingExecutor(
        {1: SearchAlreadyRunningError("search 1 already running")}
    )
    scheduler = InProcessScheduler(_all_due_source(), executor, clock=lambda: NOW)
    scheduler.refresh()

    assert scheduler.run_due() == (1, 2)
    assert [call[0] for call in executor.calls] == [1, 2]
    assert "scheduled search 1 failed" in caplog.text


def test_one_search_failure_is_isolated_from_other_due_searches(caplog):
    executor = SelectivelyFailingExecutor({1: RuntimeError("broken adapter")})
    scheduler = InProcessScheduler(_all_due_source(), executor, clock=lambda: NOW)
    scheduler.refresh()

    assert scheduler.run_due() == (1, 2)
    assert [call[0] for call in executor.calls] == [1, 2]
    assert "broken adapter" not in caplog.text


class BlockingExecutor(RecordingExecutor):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run_search(self, search_id, trigger):
        super().run_search(search_id, trigger)
        self.started.set()
        self.release.wait(1)


def test_clean_stop_waits_for_an_active_dispatch_to_finish():
    executor = BlockingExecutor()
    scheduler = InProcessScheduler(_all_due_source(), executor, clock=lambda: NOW)
    scheduler.start()
    assert executor.started.wait(1)

    assert scheduler.stop(timeout=0.01) is False
    executor.release.set()

    assert scheduler.stop(timeout=1) is True
    assert scheduler.is_running is False
    assert [call[0] for call in executor.calls] == [1]


def test_clean_stop_wakes_an_idle_scheduler():
    source = FakeSearchSource((_record(1, interval_seconds=3600),))
    scheduler = InProcessScheduler(source, RecordingExecutor(), clock=lambda: NOW)
    scheduler.start()
    assert source.loaded.wait(1)

    assert scheduler.stop(timeout=1) is True
    assert scheduler.is_running is False


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
