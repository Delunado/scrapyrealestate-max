"""Isolated per-portal spider execution: requests, results, and outcomes.

Every portal attempt in this package is bounded and self-contained: it gets
its own crawl-ready request (`PortalRunRequest`), its own unique output
file, its own subprocess timeout, and always resolves to a
`PortalRunResult` rather than an exception. `services.search_orchestration`
builds on top of this package to run every enabled portal for one search.
"""

from scrapyrealestate.execution.contract import (
    CONCLUSIVE_STATUSES,
    DEFAULT_LOG_LEVEL,
    DEFAULT_TIMEOUT_SECONDS,
    PortalRunRequest,
    PortalRunResult,
)
from scrapyrealestate.execution.output import OutputDecodeError, read_jsonl_items

__all__ = [
    "CONCLUSIVE_STATUSES",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "OutputDecodeError",
    "PortalRunRequest",
    "PortalRunResult",
    "read_jsonl_items",
]
