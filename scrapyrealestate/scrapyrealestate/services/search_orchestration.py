"""Search orchestration: resolve adapters, run enabled portals, record it all.

``SearchOrchestrationService.run_search`` is the seam between one saved
search (``SearchRecord``) and its per-run outcome. For every enabled portal
selection, in randomized order and separated by a configurable delay, it
resolves the registered adapter, executes exactly one isolated attempt
(``execution.run_portal_attempt``, which never raises), normalizes whatever
came back, and keeps only listings that are not a definite local non-match.
A conclusive attempt's matched listings are then ingested transactionally
(``services.ingestion.IngestionService``) into listings, search matches,
price history, and change events; ingestion failing is recorded on that
attempt exactly like a fetch failure, and never raises out of the run.
Every attempt - successful or not - is recorded through ``RunRepository``,
and so is the overall run. ``SearchRunLock`` guarantees at most one run of a
given search is ever in flight in this process at a time, while independent
searches proceed without waiting on each other.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from scrapyrealestate.domain.filtering import FilterOutcome, evaluate_listing
from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.values import PortalKey, RunStatus
from scrapyrealestate.execution.attempt import run_portal_attempt
from scrapyrealestate.execution.contract import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_TIMEOUT_SECONDS,
    PortalRunResult,
    utc_now,
)
from scrapyrealestate.execution.runner import SpiderRunner
from scrapyrealestate.notifiers.registry import build_default_notifier_registry
from scrapyrealestate.persistence.notifications import NotificationRepository
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
from scrapyrealestate.services.ingestion import IngestionOutcome, IngestionService
from scrapyrealestate.services.locks import SearchRunLock
from scrapyrealestate.services.notification_delivery import (
    DispatchOutcome,
    DurableNotificationDispatcher,
)

# Attempt statuses this run's overall status treats as having succeeded.
_ATTEMPT_OK = frozenset({RunStatus.SUCCESS, RunStatus.EMPTY})
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PortalAttemptOutcome:
    """One portal's recorded attempt plus what it means for this search."""

    attempt: PortalAttemptRecord
    result: PortalRunResult
    listings: tuple[NormalizedListing, ...] = ()
    matched_listings: tuple[NormalizedListing, ...] = ()
    ingestion: IngestionOutcome | None = None
    notification_deliveries: tuple[DispatchOutcome, ...] = ()
    notification_error: str | None = None


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
        ingestion: IngestionService,
        runtime_paths: RuntimePaths,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_level: str = DEFAULT_LOG_LEVEL,
        inter_portal_delay_seconds: float = 0.0,
        random_source: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
        locks: SearchRunLock | None = None,
        notification_delivery: DurableNotificationDispatcher | None = None,
        retention: Callable[[], object] | None = None,
    ) -> None:
        if inter_portal_delay_seconds < 0:
            raise ValueError("inter_portal_delay_seconds cannot be negative")
        self._registry = registry
        self._runner = runner
        self._runs = runs
        self._ingestion = ingestion
        self._runtime_paths = runtime_paths
        self._timeout_seconds = timeout_seconds
        self._log_level = log_level
        # Real deployments (the future scheduler bootstrap) should pass a
        # respectful positive delay, e.g. a couple of seconds; the default
        # of zero keeps offline tests instant without every test having to
        # say so explicitly.
        self._inter_portal_delay_seconds = inter_portal_delay_seconds
        self._random = random_source if random_source is not None else random.Random()
        self._sleep = sleep
        self._locks = locks if locks is not None else SearchRunLock()
        self._notification_delivery = notification_delivery or (
            DurableNotificationDispatcher(
                NotificationRepository(ingestion.connection),
                build_default_notifier_registry(),
            )
        )
        self._retention = retention

    def run_search(
        self,
        search_record: SearchRecord,
        trigger: TriggerKind,
        *,
        on_run_created: Callable[[SearchRunRecord], None] | None = None,
    ) -> SearchRunOutcome:
        """Run every enabled portal for ``search_record`` in isolation.

        Raises ``SearchAlreadyRunningError`` immediately, without creating
        a run record, if this search already has a run in progress in this
        process; independent searches are never blocked by this.
        """
        with self._locks.acquire(search_record.id):
            return self._run_search_locked(
                search_record, trigger, on_run_created=on_run_created
            )

    def _run_search_locked(
        self,
        search_record: SearchRecord,
        trigger: TriggerKind,
        *,
        on_run_created: Callable[[SearchRunRecord], None] | None = None,
    ) -> SearchRunOutcome:
        run = self._runs.create_run(search_record.id, trigger)
        run = self._runs.start_run(run.id, utc_now())
        if on_run_created is not None:
            on_run_created(run)

        # A fixed portal order would mean the same portal always gets hit
        # first (or last, after every other has already delayed); shuffling
        # spreads that load out across searches and runs.
        portal_selections = [p for p in search_record.portals if p.enabled]
        self._random.shuffle(portal_selections)

        outcomes = []
        for index, portal_selection in enumerate(portal_selections):
            if index > 0 and self._inter_portal_delay_seconds > 0:
                self._sleep(self._inter_portal_delay_seconds)
            outcomes.append(
                self._run_one_portal(run.id, search_record, portal_selection)
            )

        run = self._finish_run(run, tuple(outcomes))
        self._prune_operational_history()
        return SearchRunOutcome(run=run, attempts=tuple(outcomes))

    def _prune_operational_history(self) -> None:
        if self._retention is None:
            return
        try:
            self._retention()
        except Exception:  # maintenance must never change a search outcome
            logger.warning("operational history pruning failed")

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
        ingestion, ingestion_error = self._ingest(
            search_record.id, portal_selection.portal, matched, result
        )
        notification_deliveries, notification_error = self._deliver_notifications(
            ingestion
        )
        return self._finish_attempt(
            attempt,
            result,
            listings,
            matched,
            ingestion,
            ingestion_error,
            notification_deliveries,
            notification_error,
        )

    def _deliver_notifications(
        self, ingestion: IngestionOutcome | None
    ) -> tuple[tuple[DispatchOutcome, ...], str | None]:
        if ingestion is None:
            return (), None
        try:
            # Initial attempts were created atomically by ingestion. Draining
            # also picks up any retry that became due since the previous run.
            return self._notification_delivery.dispatch_available(), None
        except Exception:  # delivery infrastructure must never fail the search
            return (), "notification delivery failed"

    def _ingest(
        self,
        search_id: int,
        portal: PortalKey,
        matched: tuple[NormalizedListing, ...],
        result: PortalRunResult,
    ) -> tuple[IngestionOutcome | None, str | None]:
        if not result.conclusive:
            # No items to ingest, and disappearance reconciliation is
            # already gated on a conclusive status inside the ingestion
            # service - nothing useful to do here for an inconclusive
            # attempt.
            return None, None
        try:
            return self._ingestion.ingest_attempt(search_id, portal, matched, result.status), None
        except Exception as error:  # noqa: BLE001 - ingestion must never crash the run
            return None, f"ingestion failed: {error}"

    def _finish_attempt(
        self,
        attempt: PortalAttemptRecord,
        result: PortalRunResult,
        listings: tuple[NormalizedListing, ...],
        matched: tuple[NormalizedListing, ...],
        ingestion: IngestionOutcome | None = None,
        ingestion_error: str | None = None,
        notification_deliveries: tuple[DispatchOutcome, ...] = (),
        notification_error: str | None = None,
    ) -> PortalAttemptOutcome:
        counts = RunCounts(
            returned=len(result.items),
            matched=len(matched),
            new=ingestion.new if ingestion is not None else 0,
            changed=ingestion.changed if ingestion is not None else 0,
        )
        if ingestion_error is not None:
            # The fetch itself was conclusive; ingestion is a separate,
            # local failure and gets its own error category rather than
            # silently overwriting what actually happened on the portal.
            error_category = "ingestion_error"
            redacted_diagnostic = ingestion_error
        else:
            error_category = None if result.conclusive else result.status.value
            redacted_diagnostic = result.diagnostic
        record = self._runs.finish_attempt(
            attempt.id,
            result.status,
            result.finished_at,
            counts=counts,
            error_category=error_category,
            redacted_diagnostic=redacted_diagnostic,
        )
        return PortalAttemptOutcome(
            attempt=record,
            result=result,
            listings=listings,
            matched_listings=matched,
            ingestion=ingestion,
            notification_deliveries=notification_deliveries,
            notification_error=notification_error,
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
            new=sum(
                outcome.ingestion.new for outcome in outcomes if outcome.ingestion is not None
            ),
            changed=sum(
                outcome.ingestion.changed
                for outcome in outcomes
                if outcome.ingestion is not None
            ),
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
