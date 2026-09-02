"""Portal execution request/result contract.

`PortalRunRequest` is everything an execution component - the spider
subprocess runner, in particular - needs to run exactly one portal
attempt in isolation: which spider, which URL, and which unique output
file to write to. `PortalRunResult` is what comes back: an operational
status drawn from `RunStatus` (success, empty, timeout, transport_error,
parser_error, blocked, unavailable), the raw items a conclusive attempt
produced, and a bounded, already-safe-to-log diagnostic for anything
else. Every component in `execution/` and `services/` is built around
this contract so one misbehaving portal never needs special-casing by
its callers - see `execution.attempt.run_portal_attempt` and
`services.search_orchestration`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.portals.base import PortalRequest

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_LOG_LEVEL = "INFO"

# Attempt outcomes that produced a trustworthy (possibly empty) result, as
# opposed to one where nothing conclusive could be said about the portal's
# actual listings.
CONCLUSIVE_STATUSES = frozenset({RunStatus.SUCCESS, RunStatus.EMPTY})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _serialize(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class PortalRunRequest:
    """One isolated, crawl-ready portal attempt bound to its own output file."""

    portal: PortalKey
    spider_name: str
    start_url: str
    transaction_type: TransactionType
    output_path: Path
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    log_level: str = DEFAULT_LOG_LEVEL

    def __post_init__(self) -> None:
        if not isinstance(self.portal, PortalKey):
            raise TypeError("portal must be a PortalKey")
        if not isinstance(self.transaction_type, TransactionType):
            raise TypeError("transaction_type must be a TransactionType")

        spider_name = self.spider_name.strip()
        if not spider_name:
            raise ValueError("spider_name is required")
        object.__setattr__(self, "spider_name", spider_name)

        start_url = self.start_url.strip()
        if not start_url:
            raise ValueError("start_url is required")
        object.__setattr__(self, "start_url", start_url)

        if not isinstance(self.output_path, Path):
            object.__setattr__(self, "output_path", Path(self.output_path))

        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        log_level = self.log_level.strip() or DEFAULT_LOG_LEVEL
        object.__setattr__(self, "log_level", log_level)

    @classmethod
    def from_portal_request(
        cls,
        request: PortalRequest,
        *,
        output_path: Path,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_level: str = DEFAULT_LOG_LEVEL,
    ) -> "PortalRunRequest":
        """Build a run request from an adapter's already-validated request."""
        return cls(
            portal=request.portal,
            spider_name=request.spider_name,
            start_url=request.start_url,
            transaction_type=request.transaction_type,
            output_path=output_path,
            timeout_seconds=timeout_seconds,
            log_level=log_level,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "portal": self.portal.value,
            "spider_name": self.spider_name,
            "start_url": self.start_url,
            "transaction_type": self.transaction_type.value,
            "output_path": str(self.output_path),
            "timeout_seconds": self.timeout_seconds,
            "log_level": self.log_level,
        }


@dataclass(frozen=True, slots=True)
class PortalRunResult:
    """The operational outcome of one isolated portal attempt."""

    portal: PortalKey
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    items: tuple[Mapping[str, Any], ...] = ()
    return_code: int | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.portal, PortalKey):
            raise TypeError("portal must be a PortalKey")
        if not isinstance(self.status, RunStatus):
            raise TypeError("status must be a RunStatus")

        object.__setattr__(self, "started_at", _aware(self.started_at, "started_at"))
        object.__setattr__(self, "finished_at", _aware(self.finished_at, "finished_at"))
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")

        object.__setattr__(self, "items", tuple(self.items))
        if self.status not in CONCLUSIVE_STATUSES and self.items:
            raise ValueError(f"{self.status.value} attempts cannot carry items")

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def conclusive(self) -> bool:
        """Whether this attempt produced a trustworthy (possibly empty) result."""
        return self.status in CONCLUSIVE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "portal": self.portal.value,
            "status": self.status.value,
            "started_at": _serialize(self.started_at),
            "finished_at": _serialize(self.finished_at),
            "duration_seconds": self.duration_seconds,
            "item_count": len(self.items),
            "return_code": self.return_code,
            "diagnostic": self.diagnostic,
        }
