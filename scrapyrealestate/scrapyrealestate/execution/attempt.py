"""Resolve and execute one portal attempt without ever raising.

``run_portal_attempt`` is the seam between adapter request-building
(``PortalAdapter.build_request``/``build_request_from_search``, which can
raise ``PortalRequestError`` for a search this portal cannot serve) and
``SpiderRunner`` (which already turns its own failures into a
``PortalRunResult``; see ``execution.runner``). Wrapping both here
guarantees one portal's failure - expected or not - is always recorded as a
``PortalRunResult`` and never stops the rest of a multi-portal search run
from proceeding; ``services.search_orchestration`` relies on exactly this
guarantee when it loops over a search's enabled portals.
"""

from __future__ import annotations

from pathlib import Path

from scrapyrealestate.domain.search import NormalizedSearch
from scrapyrealestate.domain.values import RunStatus
from scrapyrealestate.execution.contract import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_TIMEOUT_SECONDS,
    PortalRunRequest,
    PortalRunResult,
    utc_now,
)
from scrapyrealestate.execution.runner import SpiderRunner
from scrapyrealestate.portals.base import PortalAdapter, PortalRequestError


def run_portal_attempt(
    adapter: PortalAdapter,
    search: NormalizedSearch,
    *,
    runner: SpiderRunner,
    output_path: Path,
    raw_url_override: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    log_level: str = DEFAULT_LOG_LEVEL,
) -> PortalRunResult:
    """Build this portal's request and run it, always returning a result.

    A raw URL override (the legacy-compatible per-search portal
    configuration) takes precedence over building a fresh URL from the
    normalized search. Anything that goes wrong resolving a request - a
    portal that cannot serve this search, an invalid override, or an
    unexpected adapter bug - is recorded as an ``UNAVAILABLE`` attempt
    instead of raising; anything unexpected from the runner itself (which
    should already be exception-safe on its own) becomes a
    ``TRANSPORT_ERROR`` attempt for the same reason.
    """
    portal = adapter.metadata.key
    started_at = utc_now()
    try:
        if raw_url_override:
            request = adapter.build_request(raw_url_override)
        else:
            request = adapter.build_request_from_search(search)
    except PortalRequestError as error:
        return PortalRunResult(
            portal=portal,
            status=RunStatus.UNAVAILABLE,
            started_at=started_at,
            finished_at=utc_now(),
            diagnostic=str(error),
        )
    except Exception as error:
        return PortalRunResult(
            portal=portal,
            status=RunStatus.UNAVAILABLE,
            started_at=started_at,
            finished_at=utc_now(),
            diagnostic=f"unexpected error building request: {error}",
        )

    run_request = PortalRunRequest.from_portal_request(
        request,
        output_path=output_path,
        timeout_seconds=timeout_seconds,
        log_level=log_level,
    )
    try:
        return runner.run(run_request)
    except Exception as error:
        return PortalRunResult(
            portal=portal,
            status=RunStatus.TRANSPORT_ERROR,
            started_at=started_at,
            finished_at=utc_now(),
            diagnostic=f"unexpected runner failure: {error}",
        )
