"""Lightweight condition-driven scheduling for enabled saved searches."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Protocol

from scrapyrealestate.persistence.runs import TriggerKind
from scrapyrealestate.persistence.searches import SearchRecord


logger = logging.getLogger(__name__)


class SearchSource(Protocol):
    """Read boundary required by the scheduler."""

    def list(self, *, enabled: bool | None = None) -> tuple[SearchRecord, ...]: ...

    def update_scheduler_state(
        self,
        search_id: int,
        *,
        next_run_at: datetime,
        last_scheduled_at: datetime | None,
    ) -> SearchRecord: ...


class SearchExecutor(Protocol):
    """Shared orchestration boundary used by scheduled and manual runs."""

    def run_search(self, search_record: SearchRecord, trigger: TriggerKind) -> object: ...


@dataclass(frozen=True, slots=True)
class _ScheduledEntry:
    search: SearchRecord
    next_run_at: datetime

    @property
    def fingerprint(self) -> tuple[int, int]:
        return self.search.version, self.search.schedule.interval_seconds


class InProcessScheduler:
    """Run enabled searches in one sleeping background thread.

    The scheduler does not periodically poll the database. It reloads once at
    startup, sleeps until the nearest UTC due time, and can be woken immediately
    with :meth:`notify_schedule_changed` after a web mutation.
    """

    def __init__(
        self,
        searches: SearchSource,
        executor: SearchExecutor,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._searches = searches
        self._executor = executor
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._condition = threading.Condition()
        self._entries: dict[int, _ScheduledEntry] = {}
        self._reload_requested = True
        self._recompute_requested = False
        self._stop_requested = False
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def next_run_times(self) -> Mapping[int, datetime]:
        """Return an immutable snapshot of computed UTC run times."""
        with self._condition:
            snapshot = {
                search_id: entry.next_run_at
                for search_id, entry in self._entries.items()
            }
        return MappingProxyType(snapshot)

    def start(self) -> None:
        """Start the scheduler once in a background thread."""
        with self._condition:
            if self.is_running:
                raise RuntimeError("scheduler is already running")
            self._stop_requested = False
            self._reload_requested = True
            self._recompute_requested = False
            self._thread = threading.Thread(
                target=self.run_forever,
                name="scrapyrealestate-scheduler",
                daemon=False,
            )
            self._thread.start()

    def stop(self, timeout: float | None = None) -> bool:
        """Wake the scheduler, request a stop, and report whether it exited."""
        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()
            thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def notify_schedule_changed(self) -> None:
        """Reload enabled searches and recompute deadlines without polling."""
        with self._condition:
            self._reload_requested = True
            self._recompute_requested = True
            self._condition.notify_all()

    def refresh(self, *, reset: bool = False) -> Mapping[int, datetime]:
        """Synchronously reload enabled searches and return their deadlines.

        This is primarily useful before ``start`` and in deterministic tests. A
        caller changing schedules while the worker runs should use
        :meth:`notify_schedule_changed` instead.
        """
        now = _as_utc(self._clock())
        records = self._searches.list(enabled=True)
        with self._condition:
            refreshed: dict[int, _ScheduledEntry] = {}
            for record in records:
                interval = _interval(record)
                persisted = _optional_utc(record.schedule.next_run_at)
                if reset or persisted is None:
                    next_run_at = now + timedelta(seconds=interval)
                    record = self._searches.update_scheduler_state(
                        record.id,
                        next_run_at=next_run_at,
                        last_scheduled_at=_optional_utc(
                            record.schedule.last_scheduled_at
                        ),
                    )
                else:
                    next_run_at = persisted
                refreshed[record.id] = _ScheduledEntry(record, next_run_at)
            self._entries = refreshed
            return MappingProxyType(
                {
                    search_id: entry.next_run_at
                    for search_id, entry in refreshed.items()
                }
            )

    def run_due(self) -> tuple[int, ...]:
        """Synchronously run every search currently due, in deadline order."""
        now = _as_utc(self._clock())
        with self._condition:
            due_ids = tuple(
                search_id
                for search_id, entry in sorted(
                    self._entries.items(), key=lambda item: (item[1].next_run_at, item[0])
                )
                if entry.next_run_at <= now
            )

        completed = []
        for search_id in due_ids:
            with self._condition:
                entry = self._entries.get(search_id)
                if entry is None or entry.next_run_at > now:
                    continue
                interval = _interval(entry.search)
                next_run_at = now + timedelta(seconds=interval)
                updated_search = self._searches.update_scheduler_state(
                    search_id,
                    next_run_at=next_run_at,
                    last_scheduled_at=entry.next_run_at,
                )
                self._entries[search_id] = _ScheduledEntry(
                    updated_search,
                    next_run_at,
                )
            try:
                self._executor.run_search(updated_search, TriggerKind.SCHEDULED)
            except Exception:
                # One search must not terminate scheduling for every other search.
                logger.error("scheduled search %s failed", search_id)
            completed.append(search_id)
        return tuple(completed)

    def run_forever(self) -> None:
        """Run the condition-driven loop in the current thread."""
        while True:
            with self._condition:
                if self._stop_requested:
                    return
                reload_requested = self._reload_requested
                recompute_requested = self._recompute_requested
                self._reload_requested = False
                self._recompute_requested = False

            if reload_requested:
                self.refresh(reset=recompute_requested)

            self.run_due()

            with self._condition:
                if self._stop_requested:
                    return
                if self._reload_requested:
                    continue
                now = _as_utc(self._clock())
                timeout = None
                if self._entries:
                    next_run_at = min(
                        entry.next_run_at for entry in self._entries.values()
                    )
                    timeout = max(0.0, (next_run_at - now).total_seconds())
                self._condition.wait(timeout=timeout)


def _interval(record: SearchRecord) -> int:
    interval = record.schedule.interval_seconds
    if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
        raise ValueError(f"search {record.id} has an invalid schedule interval")
    return interval


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _optional_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)
