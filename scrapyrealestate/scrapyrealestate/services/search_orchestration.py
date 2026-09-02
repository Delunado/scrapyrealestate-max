"""Search orchestration: resolve adapters, run enabled portals, record it all.

``SearchOrchestrationService.run_search`` is the seam between one saved
search (``SearchRecord``) and its per-run outcome. For every enabled portal
selection it resolves the registered adapter, executes exactly one isolated
attempt (``execution.run_portal_attempt``, which never raises), normalizes
whatever came back, and keeps only listings that are not a definite local
non-match. Every attempt - successful or not - is recorded through
``RunRepository``, and so is the overall run.

Ingesting matched listings into persisted state (listings, price history,
search matches, change events) is a separate, later concern; this service
only produces the filtered, normalized listings that step would consume,
plus the operational run/attempt records that already exist without it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scrapyrealestate.domain.filtering import FilterOutcome, evaluate_listing
from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.values import RunStatus
from scrapyrealestate.execution.attempt import run_portal_attempt
from scrapyrealestate.execution.contract import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_TIMEOUT_SECONDS,
    PortalRunResult,
    utc_now,
)
from scrapyrealestate.execution.runner import SpiderRunner
from scrapyrealestate.persistence.runs import (
    PortalAttemptRecord,
    RunCounts,
    RunRepository,
    SearchRunRecord,
    SearchRunStatus,
    TriggerKind,
)
from scrapyrealestate.persistence.searches import SearchPortalRecord, SearchRecord
from scrapyrealestate.portals.base import PortalAdapter
from scrapyrealestate.portals.registry import PortalRegistry
from scrapyrealestate.runtime import RuntimePaths

# Attempt statuses this run's overall status treats as having succeeded.
_ATTEMPT_OK = frozenset({RunStatus.SUCCESS, RunStatus.EMPTY})


@dataclass(frozen=True, slots=True)
class PortalAttemptOutcome:
    """One portal's recorded attempt plus what it means for this search."""

    attempt: PortalAttemptRecord
    result: PortalRunResult
    listings: tuple[NormalizedListing, ...] = ()
    matched_listings: tuple[NormalizedListing, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchRunOutcome:
    """The recorded run plus every portal's individual outcome."""

    run: SearchRunRecord
    attempts: tuple[PortalAttemptOutcome, ...] = ()


class SearchOrchestrationService:
    """Runs every enabled portal for one search, isolated and recorded."""

    def __init__(
        self,
        *,
        registry: PortalRegistry,
        runner: SpiderRunner,
        runs: RunRepository,
        runtime_paths: RuntimePaths,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_level: str = DEFAULT_LOG_LEVEL,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._runs = runs
        self._runtime_paths = runtime_paths
        self._timeout_seconds = timeout_seconds
        self._log_level = log_level

    def run_search(
        self, search_record: SearchRecord, trigger: TriggerKind
    ) -> SearchRunOutcome:
        """Run every enabled portal for ``search_record`` in isolation."""
        run = self._runs.create_run(search_record.id, trigger)
        run = self._runs.start_run(run.id, utc_now())

        outcomes = tuple(
            self._run_one_portal(run.id, search_record, portal_selection)
            for portal_selection in search_record.portals
            if portal_selection.enabled
        )

        run = self._finish_run(run, outcomes)
        return SearchRunOutcome(run=run, attempts=outcomes)

    def _run_one_portal(
        self,
        run_id: int,
        search_record: SearchRecord,
        portal_selection: SearchPortalRecord,
    ) -> PortalAttemptOutcome:
        attempt = self._runs.start_attempt(run_id, portal_selection.portal, utc_now())

        try:
            adapter = self._registry.get(portal_selection.portal)
        except KeyError as error:
            result = PortalRunResult(
                portal=portal_selection.portal,
                status=RunStatus.UNAVAILABLE,
                started_at=utc_now(),
                finished_at=utc_now(),
                diagnostic=str(error),
            )
            return self._finish_attempt(attempt, result, (), ())

        output_path = self._runtime_paths.attempt_output(
            f"search-{search_record.id}-{portal_selection.portal.value}"
        )
        result = run_portal_attempt(
            adapter,
            search_record.search,
            runner=self._runner,
            output_path=output_path,
            raw_url_override=portal_selection.raw_url_override,
            timeout_seconds=self._timeout_seconds,
            log_level=self._log_level,
        )

        listings = self._normalize(adapter, result.items)
        matched = self._filter(listings, search_record)
        return self._finish_attempt(attempt, result, listings, matched)

    def _finish_attempt(
        self,
        attempt: PortalAttemptRecord,
        result: PortalRunResult,
        listings: tuple[NormalizedListing, ...],
        matched: tuple[NormalizedListing, ...],
    ) -> PortalAttemptOutcome:
        record = self._runs.finish_attempt(
            attempt.id,
            result.status,
            result.finished_at,
            counts=RunCounts(returned=len(result.items), matched=len(matched)),
            error_category=None if result.conclusive else result.status.value,
            redacted_diagnostic=result.diagnostic,
        )
        return PortalAttemptOutcome(
            attempt=record, result=result, listings=listings, matched_listings=matched
        )

    def _normalize(
        self, adapter: PortalAdapter, items: tuple[Mapping[str, Any], ...]
    ) -> tuple[NormalizedListing, ...]:
        listings = []
        for item in items:
            try:
                listings.append(adapter.normalize_result(item))
            except (TypeError, ValueError):
                # One malformed item must not fail the whole attempt; the
                # portal's other items are still worth keeping.
                continue
        return tuple(listings)

    def _filter(
        self, listings: tuple[NormalizedListing, ...], search_record: SearchRecord
    ) -> tuple[NormalizedListing, ...]:
        # A definite non-match is excluded; a listing that can only be
        # scored as "unknown" (missing data) is kept rather than silently
        # narrowed away, matching domain.filtering's own no-match-takes-
        # precedence rule.
        search = search_record.search
        return tuple(
            listing
            for listing in listings
            if evaluate_listing(listing, search).outcome is not FilterOutcome.NO_MATCH
        )

    def _finish_run(
        self, run: SearchRunRecord, outcomes: tuple[PortalAttemptOutcome, ...]
    ) -> SearchRunRecord:
        statuses = [outcome.result.status for outcome in outcomes]
        counts = RunCounts(
            returned=sum(len(outcome.result.items) for outcome in outcomes),
            matched=sum(len(outcome.matched_listings) for outcome in outcomes),
        )
        status = _overall_status(statuses)

        error_category = None
        diagnostic = None
        if status is not SearchRunStatus.SUCCESS:
            error_category = status.value
            if not outcomes:
                diagnostic = "no enabled portals"
            else:
                failing = [o for o in outcomes if not o.result.conclusive]
                diagnostic = "; ".join(
                    f"{o.result.portal.value}: {o.result.diagnostic or o.result.status.value}"
                    for o in failing
                ) or None

        return self._runs.finish_run(
            run.id,
            status,
            utc_now(),
            counts=counts,
            error_category=error_category,
            redacted_diagnostic=diagnostic,
        )


def _overall_status(statuses: list[RunStatus]) -> SearchRunStatus:
    if not statuses:
        return SearchRunStatus.FAILED
    ok = [status for status in statuses if status in _ATTEMPT_OK]
    if len(ok) == len(statuses):
        return SearchRunStatus.SUCCESS
    if ok:
        return SearchRunStatus.PARTIAL
    return SearchRunStatus.FAILED
