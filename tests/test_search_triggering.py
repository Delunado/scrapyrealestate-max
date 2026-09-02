from dataclasses import replace

import pytest

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import TransactionType
from scrapyrealestate.persistence.runs import TriggerKind
from scrapyrealestate.persistence.searches import SearchRecord, SearchScheduleRecord
from scrapyrealestate.services.locks import SearchAlreadyRunningError
from scrapyrealestate.services.search_triggering import SearchTriggerService


def _record(*, enabled=True):
    return SearchRecord(
        id=7,
        search=NormalizedSearch(
            name="Centro",
            transaction_type=TransactionType.RENT,
            filters=SearchFilters(),
        ),
        enabled=enabled,
        version=1,
        schedule=SearchScheduleRecord(interval_seconds=600),
        portals=(),
        created_at="2026-09-02T09:00:00Z",
        updated_at="2026-09-02T09:00:00Z",
    )


class SearchLookup:
    def __init__(self, record):
        self.record = record
        self.ids = []

    def get(self, search_id):
        self.ids.append(search_id)
        return self.record


class Orchestration:
    def __init__(self, result="completed"):
        self.result = result
        self.calls = []

    def run_search(self, record, trigger):
        self.calls.append((record, trigger))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("trigger", [TriggerKind.MANUAL, TriggerKind.SCHEDULED])
def test_manual_and_scheduled_runs_use_the_same_orchestration_api(trigger):
    lookup = SearchLookup(_record())
    orchestration = Orchestration()
    service = SearchTriggerService(lookup, orchestration)

    assert service.run_search(7, trigger) == "completed"
    assert lookup.ids == [7]
    assert orchestration.calls == [(lookup.record, trigger)]


def test_scheduled_snapshot_is_skipped_if_search_was_disabled_before_dispatch():
    lookup = SearchLookup(replace(_record(), enabled=False))
    orchestration = Orchestration()

    result = SearchTriggerService(lookup, orchestration).run_search(
        7, TriggerKind.SCHEDULED
    )

    assert result is None
    assert orchestration.calls == []


def test_manual_run_is_allowed_for_a_disabled_search():
    lookup = SearchLookup(replace(_record(), enabled=False))
    orchestration = Orchestration()

    SearchTriggerService(lookup, orchestration).run_search(7, TriggerKind.MANUAL)

    assert orchestration.calls == [(lookup.record, TriggerKind.MANUAL)]


def test_overlap_error_from_shared_orchestration_is_preserved():
    overlap = SearchAlreadyRunningError("search 7 is already running")
    service = SearchTriggerService(SearchLookup(_record()), Orchestration(overlap))

    with pytest.raises(SearchAlreadyRunningError):
        service.run_search(7, TriggerKind.MANUAL)
