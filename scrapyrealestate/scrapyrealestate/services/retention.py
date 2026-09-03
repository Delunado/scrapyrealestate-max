"""Bounded pruning of verbose operational history."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from scrapyrealestate.persistence.database import transaction


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    diagnostic_days: int = 30
    delivery_attempt_days: int = 90
    max_terminal_delivery_attempts: int = 10_000

    def __post_init__(self) -> None:
        for name in ("diagnostic_days", "delivery_attempt_days"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3650:
                raise ValueError(f"{name} must be between 1 and 3650")
        maximum = self.max_terminal_delivery_attempts
        if (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= 1_000_000
        ):
            raise ValueError(
                "max_terminal_delivery_attempts must be between 1 and 1000000"
            )


@dataclass(frozen=True, slots=True)
class RetentionOutcome:
    run_diagnostics_cleared: int = 0
    attempt_diagnostics_cleared: int = 0
    delivery_attempts_deleted: int = 0


class OperationalRetentionService:
    """Prune bounded operational detail without touching property history."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        policy: RetentionPolicy = RetentionPolicy(),
    ) -> None:
        self.connection = connection
        self.policy = policy

    def prune(self, *, now: datetime | None = None) -> RetentionOutcome:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        diagnostic_cutoff = _timestamp(
            current - timedelta(days=self.policy.diagnostic_days)
        )
        delivery_cutoff = _timestamp(
            current - timedelta(days=self.policy.delivery_attempt_days)
        )
        with transaction(self.connection, immediate=True):
            runs = self.connection.execute(
                """
                UPDATE search_runs SET redacted_diagnostic = NULL
                WHERE redacted_diagnostic IS NOT NULL
                  AND datetime(coalesce(finished_at, created_at)) < datetime(?)
                """,
                (diagnostic_cutoff,),
            ).rowcount
            attempts = self.connection.execute(
                """
                UPDATE portal_attempts SET redacted_diagnostic = NULL
                WHERE redacted_diagnostic IS NOT NULL
                  AND finished_at IS NOT NULL
                  AND datetime(finished_at) < datetime(?)
                """,
                (diagnostic_cutoff,),
            ).rowcount
            deleted_for_age = self.connection.execute(
                """
                DELETE FROM notification_delivery_attempts
                WHERE status IN ('succeeded', 'failed')
                  AND completed_at IS NOT NULL
                  AND datetime(completed_at) < datetime(?)
                """,
                (delivery_cutoff,),
            ).rowcount
            deleted_for_cap = self.connection.execute(
                """
                DELETE FROM notification_delivery_attempts
                WHERE id IN (
                    SELECT id FROM notification_delivery_attempts
                    WHERE status IN ('succeeded', 'failed')
                    ORDER BY datetime(completed_at) DESC, id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.policy.max_terminal_delivery_attempts,),
            ).rowcount
        return RetentionOutcome(
            run_diagnostics_cleared=runs,
            attempt_diagnostics_cleared=attempts,
            delivery_attempts_deleted=deleted_for_age + deleted_for_cap,
        )


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
