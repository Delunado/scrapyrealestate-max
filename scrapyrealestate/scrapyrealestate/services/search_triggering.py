"""One entry point for manual and scheduled saved-search execution."""

from __future__ import annotations

from typing import Protocol

from scrapyrealestate.persistence.runs import TriggerKind
from scrapyrealestate.persistence.searches import SearchRecord


class SearchLookup(Protocol):
    def get(self, search_id: int) -> SearchRecord: ...


class SearchOrchestrator(Protocol):
    def run_search(
        self, search_record: SearchRecord, trigger: TriggerKind, *, on_run_created=None
    ) -> object: ...


class SearchTriggerService:
    """Load current saved-search state and invoke lock-protected orchestration."""

    def __init__(self, searches: SearchLookup, orchestration: SearchOrchestrator) -> None:
        self._searches = searches
        self._orchestration = orchestration

    def run_search(
        self, search_id: int, trigger: TriggerKind, *, on_run_created=None
    ) -> object | None:
        """Run one saved search through the shared orchestration boundary.

        A search disabled after the scheduler loaded its earlier snapshot is skipped.
        Manual runs remain available for disabled searches so a user can explicitly
        test one before enabling its schedule.
        """
        if not isinstance(trigger, TriggerKind):
            raise TypeError("trigger must be a TriggerKind")
        search_record = self._searches.get(search_id)
        if trigger is TriggerKind.SCHEDULED and not search_record.enabled:
            return None
        if on_run_created is None:
            return self._orchestration.run_search(search_record, trigger)
        return self._orchestration.run_search(
            search_record, trigger, on_run_created=on_run_created
        )
