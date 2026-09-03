"""Repositories for saved searches, schedules, and portal selections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.persistence.database import transaction


class SearchNotFoundError(LookupError):
    """The requested saved search does not exist."""


class SearchConflictError(RuntimeError):
    """A saved search changed since the caller last read it."""


@dataclass(frozen=True, slots=True)
class SearchScheduleRecord:
    interval_seconds: int
    next_run_at: str | None = None
    last_scheduled_at: str | None = None


@dataclass(frozen=True, slots=True)
class SearchPortalRecord:
    portal: PortalKey
    raw_url_override: str | None = None
    adapter_options: dict[str, Any] | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.portal, PortalKey):
            raise TypeError("portal must be a PortalKey")
        object.__setattr__(self, "adapter_options", dict(self.adapter_options or {}))


@dataclass(frozen=True, slots=True)
class SearchRecord:
    id: int
    search: NormalizedSearch
    enabled: bool
    version: int
    schedule: SearchScheduleRecord
    portals: tuple[SearchPortalRecord, ...]
    created_at: str
    updated_at: str
    unknown_filter_fields: dict[str, Any] | None = None


class SearchRepository:
    """Persist a complete saved search aggregate on one SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self,
        search: NormalizedSearch,
        *,
        interval_seconds: int,
        portals: tuple[SearchPortalRecord, ...] = (),
        enabled: bool = True,
    ) -> SearchRecord:
        with transaction(self.connection, immediate=True):
            search_id = self.connection.execute(
                """
                INSERT INTO searches (
                    name, transaction_type, filters_json, enabled
                ) VALUES (?, ?, ?, ?) RETURNING id
                """,
                (
                    search.name,
                    search.transaction_type.value,
                    _json(search.filters.to_dict()),
                    int(enabled),
                ),
            ).fetchone()[0]
            self.connection.execute(
                """
                INSERT INTO search_schedules (search_id, interval_seconds)
                VALUES (?, ?)
                """,
                (search_id, interval_seconds),
            )
            self._replace_portals(search_id, portals)
        return self.get(search_id)

    def get(self, search_id: int) -> SearchRecord:
        row = self.connection.execute(
            """
            SELECT s.*, ss.interval_seconds, ss.next_run_at, ss.last_scheduled_at
            FROM searches AS s
            JOIN search_schedules AS ss ON ss.search_id = s.id
            WHERE s.id = ?
            """,
            (search_id,),
        ).fetchone()
        if row is None:
            raise SearchNotFoundError(f"search {search_id} does not exist")
        portal_rows = self.connection.execute(
            """
            SELECT portal_key, raw_url_override, adapter_options_json, enabled
            FROM search_portals WHERE search_id = ? ORDER BY portal_key
            """,
            (search_id,),
        ).fetchall()
        return _record(row, portal_rows)

    def list(self, *, enabled: bool | None = None) -> tuple[SearchRecord, ...]:
        condition = "" if enabled is None else "WHERE s.enabled = ?"
        parameters = () if enabled is None else (int(enabled),)
        rows = self.connection.execute(
            f"""
            SELECT s.id FROM searches AS s
            {condition}
            ORDER BY s.name COLLATE NOCASE, s.id
            """,  # noqa: S608 - condition is selected from fixed strings above
            parameters,
        ).fetchall()
        return tuple(self.get(row["id"]) for row in rows)

    def update(
        self,
        search_id: int,
        search: NormalizedSearch,
        *,
        expected_version: int,
    ) -> SearchRecord:
        now = _utc_now()
        cursor = self.connection.execute(
            """
            UPDATE searches
            SET name = ?, transaction_type = ?, filters_json = ?,
                version = version + 1, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                search.name,
                search.transaction_type.value,
                _json(self._merged_filters(search_id, search.filters)),
                now,
                search_id,
                expected_version,
            ),
        )
        self._raise_update_error(search_id, cursor.rowcount)
        return self.get(search_id)

    def update_configuration(
        self,
        search_id: int,
        search: NormalizedSearch,
        *,
        interval_seconds: int,
        portals: tuple[SearchPortalRecord, ...],
        expected_version: int,
        enabled: bool,
    ) -> SearchRecord:
        """Atomically update a complete web-editable search aggregate.

        Unknown filter keys already stored by a newer application version are
        retained, while the submitted normalized fields replace only the keys
        this version understands.
        """
        now = _utc_now()
        with transaction(self.connection, immediate=True):
            cursor = self.connection.execute(
                """
                UPDATE searches
                SET name = ?, transaction_type = ?, filters_json = ?, enabled = ?,
                    version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    search.name,
                    search.transaction_type.value,
                    _json(self._merged_filters(search_id, search.filters)),
                    int(enabled),
                    now,
                    search_id,
                    expected_version,
                ),
            )
            self._raise_update_error(search_id, cursor.rowcount)
            self.connection.execute(
                """
                UPDATE search_schedules
                SET interval_seconds = ?, next_run_at = NULL,
                    last_scheduled_at = NULL, updated_at = ?
                WHERE search_id = ?
                """,
                (interval_seconds, now, search_id),
            )
            self.connection.execute(
                "DELETE FROM search_portals WHERE search_id = ?", (search_id,)
            )
            self._replace_portals(search_id, portals)
        return self.get(search_id)

    def set_enabled(self, search_id: int, enabled: bool) -> SearchRecord:
        cursor = self.connection.execute(
            """
            UPDATE searches SET enabled = ?, version = version + 1, updated_at = ?
            WHERE id = ?
            """,
            (int(enabled), _utc_now(), search_id),
        )
        self._raise_update_error(search_id, cursor.rowcount)
        return self.get(search_id)

    def update_schedule(
        self,
        search_id: int,
        *,
        interval_seconds: int,
        next_run_at: str | None = None,
        last_scheduled_at: str | None = None,
    ) -> SearchRecord:
        cursor = self.connection.execute(
            """
            UPDATE search_schedules
            SET interval_seconds = ?, next_run_at = ?, last_scheduled_at = ?,
                updated_at = ?
            WHERE search_id = ?
            """,
            (
                interval_seconds,
                next_run_at,
                last_scheduled_at,
                _utc_now(),
                search_id,
            ),
        )
        self._raise_update_error(search_id, cursor.rowcount)
        return self.get(search_id)

    def update_scheduler_state(
        self,
        search_id: int,
        *,
        next_run_at: datetime,
        last_scheduled_at: datetime | None,
    ) -> SearchRecord:
        """Persist scheduler-owned UTC state without changing the interval."""
        cursor = self.connection.execute(
            """
            UPDATE search_schedules
            SET next_run_at = ?, last_scheduled_at = ?, updated_at = ?
            WHERE search_id = ?
            """,
            (
                _timestamp(next_run_at),
                _optional_timestamp(last_scheduled_at),
                _utc_now(),
                search_id,
            ),
        )
        self._raise_update_error(search_id, cursor.rowcount)
        return self.get(search_id)

    def replace_portals(
        self, search_id: int, portals: tuple[SearchPortalRecord, ...]
    ) -> SearchRecord:
        if not self._exists(search_id):
            raise SearchNotFoundError(f"search {search_id} does not exist")
        with transaction(self.connection, immediate=True):
            self.connection.execute(
                "DELETE FROM search_portals WHERE search_id = ?", (search_id,)
            )
            self._replace_portals(search_id, portals)
            self.connection.execute(
                """
                UPDATE searches SET version = version + 1, updated_at = ? WHERE id = ?
                """,
                (_utc_now(), search_id),
            )
        return self.get(search_id)

    def delete(self, search_id: int) -> bool:
        return bool(
            self.connection.execute(
                "DELETE FROM searches WHERE id = ?", (search_id,)
            ).rowcount
        )

    def _replace_portals(
        self, search_id: int, portals: tuple[SearchPortalRecord, ...]
    ) -> None:
        self.connection.executemany(
            """
            INSERT INTO search_portals (
                search_id, portal_key, raw_url_override,
                adapter_options_json, enabled
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    search_id,
                    portal.portal.value,
                    portal.raw_url_override,
                    _json(portal.adapter_options),
                    int(portal.enabled),
                )
                for portal in portals
            ),
        )

    def _exists(self, search_id: int) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM searches WHERE id = ?", (search_id,)
            ).fetchone()
            is not None
        )

    def _merged_filters(
        self, search_id: int, filters: SearchFilters
    ) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT filters_json FROM searches WHERE id = ?", (search_id,)
        ).fetchone()
        if row is None:
            raise SearchNotFoundError(f"search {search_id} does not exist")
        stored = json.loads(row["filters_json"])
        known = filters.to_dict()
        unknown = {key: value for key, value in stored.items() if key not in known}
        return {**unknown, **known}

    def _raise_update_error(self, search_id: int, rowcount: int) -> None:
        if rowcount:
            return
        if self._exists(search_id):
            raise SearchConflictError(f"search {search_id} has changed")
        raise SearchNotFoundError(f"search {search_id} does not exist")


def _record(row: sqlite3.Row, portal_rows: list[sqlite3.Row]) -> SearchRecord:
    stored_filters = json.loads(row["filters_json"])
    known_names = set(SearchFilters().to_dict())
    filters = SearchFilters.from_dict(
        {key: value for key, value in stored_filters.items() if key in known_names}
    )
    return SearchRecord(
        id=row["id"],
        search=NormalizedSearch(
            name=row["name"],
            transaction_type=TransactionType(row["transaction_type"]),
            filters=filters,
        ),
        enabled=bool(row["enabled"]),
        version=row["version"],
        schedule=SearchScheduleRecord(
            interval_seconds=row["interval_seconds"],
            next_run_at=row["next_run_at"],
            last_scheduled_at=row["last_scheduled_at"],
        ),
        portals=tuple(
            SearchPortalRecord(
                portal=PortalKey(portal["portal_key"]),
                raw_url_override=portal["raw_url_override"],
                adapter_options=json.loads(portal["adapter_options_json"]),
                enabled=bool(portal["enabled"]),
            )
            for portal in portal_rows
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        unknown_filter_fields={
            key: value for key, value in stored_filters.items() if key not in known_names
        },
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _optional_timestamp(value: datetime | None) -> str | None:
    return _timestamp(value) if value is not None else None
