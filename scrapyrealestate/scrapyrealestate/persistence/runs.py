"""Persistence and status queries for search runs and portal attempts."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from scrapyrealestate.domain.values import PortalKey, RunStatus


class TriggerKind(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class SearchRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunCounts:
    returned: int = 0
    matched: int = 0
    new: int = 0
    changed: int = 0


@dataclass(frozen=True, slots=True)
class PortalAttemptRecord:
    id: int
    search_run_id: int
    portal: PortalKey
    attempt_number: int
    status: str
    started_at: str
    finished_at: str | None
    counts: RunCounts
    error_category: str | None
    redacted_diagnostic: str | None

    @property
    def duration_seconds(self) -> float | None:
        return _duration(self.started_at, self.finished_at)


@dataclass(frozen=True, slots=True)
class SearchRunRecord:
    id: int
    search_id: int
    trigger: TriggerKind
    status: SearchRunStatus
    scheduled_for: str | None
    started_at: str | None
    finished_at: str | None
    counts: RunCounts
    error_category: str | None
    redacted_diagnostic: str | None
    created_at: str

    @property
    def duration_seconds(self) -> float | None:
        return _duration(self.started_at, self.finished_at)


@dataclass(frozen=True, slots=True)
class LatestSearchStatus:
    run: SearchRunRecord | None
    attempts: tuple[PortalAttemptRecord, ...]
    next_run_at: str | None


@dataclass(frozen=True, slots=True)
class PortalHealthSummary:
    portal: PortalKey
    sample_size: int
    latest_status: str
    latest_attempt_at: str
    success_count: int = 0
    empty_count: int = 0
    blocked_count: int = 0
    parser_error_count: int = 0
    unavailable_count: int = 0
    transport_error_count: int = 0
    timeout_count: int = 0
    running_count: int = 0

    @property
    def conclusive_count(self) -> int:
        return self.success_count + self.empty_count


class RunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_run(
        self,
        search_id: int,
        trigger: TriggerKind,
        *,
        scheduled_for: datetime | None = None,
    ) -> SearchRunRecord:
        run_id = self.connection.execute(
            """
            INSERT INTO search_runs (search_id, trigger_kind, scheduled_for)
            VALUES (?, ?, ?) RETURNING id
            """,
            (search_id, trigger.value, _optional_timestamp(scheduled_for)),
        ).fetchone()[0]
        return self.get_run(run_id)

    def start_run(self, run_id: int, started_at: datetime) -> SearchRunRecord:
        self._update_run(
            run_id,
            "status = 'running', started_at = ?",
            (_timestamp(started_at),),
            allowed_status=SearchRunStatus.PENDING,
        )
        return self.get_run(run_id)

    def finish_run(
        self,
        run_id: int,
        status: SearchRunStatus,
        finished_at: datetime,
        *,
        counts: RunCounts = RunCounts(),
        error_category: str | None = None,
        redacted_diagnostic: str | None = None,
    ) -> SearchRunRecord:
        if status in {SearchRunStatus.PENDING, SearchRunStatus.RUNNING}:
            raise ValueError("finished run requires a terminal status")
        self._update_run(
            run_id,
            """
            status = ?, finished_at = ?, returned_count = ?, matched_count = ?,
            new_count = ?, changed_count = ?, error_category = ?,
            redacted_diagnostic = ?
            """,
            (
                status.value,
                _timestamp(finished_at),
                counts.returned,
                counts.matched,
                counts.new,
                counts.changed,
                error_category,
                _diagnostic(redacted_diagnostic),
            ),
            allowed_status=SearchRunStatus.RUNNING,
        )
        return self.get_run(run_id)

    def start_attempt(
        self,
        run_id: int,
        portal: PortalKey,
        started_at: datetime,
        *,
        attempt_number: int = 1,
    ) -> PortalAttemptRecord:
        attempt_id = self.connection.execute(
            """
            INSERT INTO portal_attempts (
                search_run_id, portal_key, attempt_number, started_at
            ) VALUES (?, ?, ?, ?) RETURNING id
            """,
            (run_id, portal.value, attempt_number, _timestamp(started_at)),
        ).fetchone()[0]
        return self.get_attempt(attempt_id)

    def finish_attempt(
        self,
        attempt_id: int,
        status: RunStatus,
        finished_at: datetime,
        *,
        counts: RunCounts = RunCounts(),
        error_category: str | None = None,
        redacted_diagnostic: str | None = None,
    ) -> PortalAttemptRecord:
        cursor = self.connection.execute(
            """
            UPDATE portal_attempts SET
                status = ?, finished_at = ?, returned_count = ?, matched_count = ?,
                new_count = ?, changed_count = ?, error_category = ?,
                redacted_diagnostic = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                status.value,
                _timestamp(finished_at),
                counts.returned,
                counts.matched,
                counts.new,
                counts.changed,
                error_category,
                _diagnostic(redacted_diagnostic),
                attempt_id,
            ),
        )
        if not cursor.rowcount:
            raise LookupError(f"running portal attempt {attempt_id} does not exist")
        return self.get_attempt(attempt_id)

    def get_run(self, run_id: int) -> SearchRunRecord:
        row = self.connection.execute(
            "SELECT * FROM search_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"search run {run_id} does not exist")
        return _run_record(row)

    def get_attempt(self, attempt_id: int) -> PortalAttemptRecord:
        row = self.connection.execute(
            "SELECT * FROM portal_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"portal attempt {attempt_id} does not exist")
        return _attempt_record(row)

    def attempts_for_run(self, run_id: int) -> tuple[PortalAttemptRecord, ...]:
        rows = self.connection.execute(
            """
            SELECT * FROM portal_attempts
            WHERE search_run_id = ? ORDER BY portal_key, attempt_number
            """,
            (run_id,),
        ).fetchall()
        return tuple(_attempt_record(row) for row in rows)

    def latest_status(self, search_id: int) -> LatestSearchStatus:
        row = self.connection.execute(
            """
            SELECT * FROM search_runs
            WHERE search_id = ? ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (search_id,),
        ).fetchone()
        schedule = self.connection.execute(
            "SELECT next_run_at FROM search_schedules WHERE search_id = ?",
            (search_id,),
        ).fetchone()
        run = _run_record(row) if row is not None else None
        return LatestSearchStatus(
            run=run,
            attempts=self.attempts_for_run(run.id) if run is not None else (),
            next_run_at=schedule["next_run_at"] if schedule is not None else None,
        )

    def portal_health(
        self, *, attempts_per_portal: int = 20
    ) -> tuple[PortalHealthSummary, ...]:
        if (
            isinstance(attempts_per_portal, bool)
            or not isinstance(attempts_per_portal, int)
            or not 1 <= attempts_per_portal <= 100
        ):
            raise ValueError("attempts_per_portal must be between 1 and 100")
        rows = self.connection.execute(
            """
            SELECT * FROM (
                SELECT portal_key, status, started_at, id,
                       row_number() OVER (
                           PARTITION BY portal_key
                           ORDER BY datetime(started_at) DESC, id DESC
                       ) AS recent_rank
                FROM portal_attempts
            ) AS recent
            WHERE recent_rank <= ?
            ORDER BY portal_key, recent_rank
            """,
            (attempts_per_portal,),
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["portal_key"], []).append(row)
        summaries = []
        for portal_key, attempts in grouped.items():
            counts = Counter(row["status"] for row in attempts)
            summaries.append(
                PortalHealthSummary(
                    portal=PortalKey(portal_key),
                    sample_size=len(attempts),
                    latest_status=attempts[0]["status"],
                    latest_attempt_at=attempts[0]["started_at"],
                    success_count=counts[RunStatus.SUCCESS.value],
                    empty_count=counts[RunStatus.EMPTY.value],
                    blocked_count=counts[RunStatus.BLOCKED.value],
                    parser_error_count=counts[RunStatus.PARSER_ERROR.value],
                    unavailable_count=counts[RunStatus.UNAVAILABLE.value],
                    transport_error_count=counts[RunStatus.TRANSPORT_ERROR.value],
                    timeout_count=counts[RunStatus.TIMEOUT.value],
                    running_count=counts[SearchRunStatus.RUNNING.value],
                )
            )
        return tuple(summaries)

    def _update_run(
        self,
        run_id: int,
        assignments: str,
        parameters: tuple[object, ...],
        *,
        allowed_status: SearchRunStatus,
    ) -> None:
        cursor = self.connection.execute(
            f"UPDATE search_runs SET {assignments} WHERE id = ? AND status = ?",  # noqa: S608
            (*parameters, run_id, allowed_status.value),
        )
        if not cursor.rowcount:
            raise LookupError(
                f"{allowed_status.value} search run {run_id} does not exist"
            )


def _counts(row: sqlite3.Row) -> RunCounts:
    return RunCounts(
        returned=row["returned_count"],
        matched=row["matched_count"],
        new=row["new_count"],
        changed=row["changed_count"],
    )


def _run_record(row: sqlite3.Row) -> SearchRunRecord:
    return SearchRunRecord(
        id=row["id"],
        search_id=row["search_id"],
        trigger=TriggerKind(row["trigger_kind"]),
        status=SearchRunStatus(row["status"]),
        scheduled_for=row["scheduled_for"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        counts=_counts(row),
        error_category=row["error_category"],
        redacted_diagnostic=row["redacted_diagnostic"],
        created_at=row["created_at"],
    )


def _attempt_record(row: sqlite3.Row) -> PortalAttemptRecord:
    return PortalAttemptRecord(
        id=row["id"],
        search_run_id=row["search_run_id"],
        portal=PortalKey(row["portal_key"]),
        attempt_number=row["attempt_number"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        counts=_counts(row),
        error_category=row["error_category"],
        redacted_diagnostic=row["redacted_diagnostic"],
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_timestamp(value: datetime | None) -> str | None:
    return _timestamp(value) if value is not None else None


def _duration(started_at: str | None, finished_at: str | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return (finished - started).total_seconds()


def _diagnostic(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:2000]
