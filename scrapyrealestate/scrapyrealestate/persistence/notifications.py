"""Repositories for notification channels, events, and delivery attempts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from scrapyrealestate.domain.notification import (
    NotificationEvent,
    NotificationEventType,
    NotificationPreferences,
)
from scrapyrealestate.domain.values import PortalKey
from scrapyrealestate.persistence.database import transaction


MASKED_SECRET = "********"


class NotificationProvider(StrEnum):
    TELEGRAM = "telegram"
    NTFY = "ntfy"
    WEBHOOK = "webhook"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NotificationChannelRecord:
    id: int
    name: str
    provider: NotificationProvider
    config: dict[str, Any]
    secret_config: dict[str, Any]
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class NotificationChannelDeliveryConfig:
    """Credential-bearing channel view available only to delivery services."""

    id: int
    name: str
    provider: NotificationProvider
    config: dict[str, Any]
    secret_config: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class NotificationEventRecord:
    id: int
    search_id: int
    listing_id: int | None
    event_type: NotificationEventType
    deduplication_key: str
    payload: dict[str, Any]
    occurred_at: str
    created_at: str


@dataclass(frozen=True, slots=True)
class EventCreationResult:
    event: NotificationEventRecord
    created: bool


@dataclass(frozen=True, slots=True)
class DeliveryAttemptRecord:
    id: int
    event_id: int
    channel_id: int
    attempt_number: int
    status: DeliveryStatus
    claimed_at: str | None
    completed_at: str | None
    error_category: str | None
    redacted_diagnostic: str | None
    provider_message_id: str | None
    created_at: str
    available_at: str
    lease_expires_at: str | None


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    attempt: DeliveryAttemptRecord
    claim_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DeliveryCompletion:
    attempt: DeliveryAttemptRecord
    retry: DeliveryAttemptRecord | None = None


@dataclass(frozen=True, slots=True)
class NotificationChannelTestRecord:
    id: int
    channel_id: int
    success: bool
    error_category: str | None
    redacted_diagnostic: str | None
    tested_at: str


class StaleDeliveryClaimError(RuntimeError):
    """A worker tried to finish a claim it no longer owns."""


class NotificationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_channel(
        self,
        name: str,
        provider: NotificationProvider,
        *,
        config: dict[str, Any] | None = None,
        secret_config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> NotificationChannelRecord:
        channel_id = self.connection.execute(
            """
            INSERT INTO notification_channels (
                name, provider, config_json, secret_config_json, enabled
            ) VALUES (?, ?, ?, ?, ?) RETURNING id
            """,
            (
                name,
                provider.value,
                _json(config or {}),
                _json(secret_config or {}),
                int(enabled),
            ),
        ).fetchone()[0]
        return self.get_channel(channel_id)

    def get_channel(self, channel_id: int) -> NotificationChannelRecord:
        row = self.connection.execute(
            "SELECT * FROM notification_channels WHERE id = ?", (channel_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"notification channel {channel_id} does not exist")
        return _channel_record(row)

    def list_channels(self) -> tuple[NotificationChannelRecord, ...]:
        return tuple(
            _channel_record(row)
            for row in self.connection.execute(
                "SELECT * FROM notification_channels ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        )

    def recent_events(
        self, *, limit: int = 10
    ) -> tuple[NotificationEventRecord, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self.connection.execute(
            """
            SELECT * FROM notification_events
            ORDER BY datetime(occurred_at) DESC, id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(_event_record(row) for row in rows)

    def record_channel_test(
        self,
        channel_id: int,
        *,
        success: bool,
        tested_at: datetime,
        error_category: str | None = None,
        diagnostic: str | None = None,
    ) -> NotificationChannelTestRecord:
        if success:
            error_category = None
            diagnostic = None
        elif not error_category or not error_category.strip():
            raise ValueError("failed channel test requires an error category")
        test_id = self.connection.execute(
            """
            INSERT INTO notification_channel_tests (
                channel_id, success, error_category, redacted_diagnostic, tested_at
            ) VALUES (?, ?, ?, ?, ?) RETURNING id
            """,
            (
                channel_id,
                int(success),
                error_category.strip() if error_category else None,
                _bounded_diagnostic(diagnostic),
                _timestamp(tested_at),
            ),
        ).fetchone()[0]
        row = self.connection.execute(
            "SELECT * FROM notification_channel_tests WHERE id = ?", (test_id,)
        ).fetchone()
        return _channel_test_record(row)

    def latest_channel_test(
        self, channel_id: int
    ) -> NotificationChannelTestRecord | None:
        row = self.connection.execute(
            """
            SELECT * FROM notification_channel_tests
            WHERE channel_id = ? ORDER BY datetime(tested_at) DESC, id DESC LIMIT 1
            """,
            (channel_id,),
        ).fetchone()
        return _channel_test_record(row) if row is not None else None

    def update_channel(
        self,
        channel_id: int,
        *,
        name: str,
        config: dict[str, Any],
        secret_config: dict[str, Any] | None = None,
        enabled: bool,
    ) -> NotificationChannelRecord:
        assignments = "name = ?, config_json = ?, enabled = ?, updated_at = ?"
        parameters: tuple[Any, ...] = (
            name,
            _json(config),
            int(enabled),
            _utc_now(),
        )
        if secret_config is not None:
            assignments += ", secret_config_json = ?"
            parameters += (_json(secret_config),)
        cursor = self.connection.execute(
            f"UPDATE notification_channels SET {assignments} WHERE id = ?",  # noqa: S608
            (*parameters, channel_id),
        )
        if not cursor.rowcount:
            raise LookupError(f"notification channel {channel_id} does not exist")
        return self.get_channel(channel_id)

    def delete_channel(self, channel_id: int) -> bool:
        return bool(
            self.connection.execute(
                "DELETE FROM notification_channels WHERE id = ?", (channel_id,)
            ).rowcount
        )

    def assign_channel(self, search_id: int, channel_id: int) -> bool:
        return bool(
            self.connection.execute(
                """
                INSERT INTO search_notification_channels (search_id, channel_id)
                VALUES (?, ?) ON CONFLICT DO NOTHING
                """,
                (search_id, channel_id),
            ).rowcount
        )

    def unassign_channel(self, search_id: int, channel_id: int) -> bool:
        return bool(
            self.connection.execute(
                """
                DELETE FROM search_notification_channels
                WHERE search_id = ? AND channel_id = ?
                """,
                (search_id, channel_id),
            ).rowcount
        )

    def update_search_settings(
        self,
        search_id: int,
        *,
        channel_ids: frozenset[int],
        preferences: NotificationPreferences,
    ) -> None:
        """Atomically replace channel assignments and event preferences."""
        if not self._search_exists(search_id):
            raise LookupError(f"search {search_id} does not exist")
        known_ids = {
            row["id"]
            for row in self.connection.execute(
                "SELECT id FROM notification_channels"
            ).fetchall()
        }
        if not channel_ids <= known_ids:
            raise LookupError("one or more notification channels do not exist")
        with transaction(self.connection, immediate=True):
            self.connection.execute(
                "DELETE FROM search_notification_channels WHERE search_id = ?",
                (search_id,),
            )
            self.connection.executemany(
                """
                INSERT INTO search_notification_channels (search_id, channel_id)
                VALUES (?, ?)
                """,
                ((search_id, channel_id) for channel_id in sorted(channel_ids)),
            )
            self.set_event_preferences(search_id, preferences)

    def channels_for_search(
        self, search_id: int, *, enabled_only: bool = True
    ) -> tuple[NotificationChannelRecord, ...]:
        enabled_clause = "AND c.enabled = 1" if enabled_only else ""
        rows = self.connection.execute(
            f"""
            SELECT c.* FROM notification_channels AS c
            JOIN search_notification_channels AS sc ON sc.channel_id = c.id
            WHERE sc.search_id = ? {enabled_clause}
            ORDER BY c.name COLLATE NOCASE, c.id
            """,  # noqa: S608
            (search_id,),
        ).fetchall()
        return tuple(_channel_record(row) for row in rows)

    def delivery_channels_for_search(
        self, search_id: int
    ) -> tuple[NotificationChannelDeliveryConfig, ...]:
        """Return enabled assigned channels, including delivery credentials.

        Normal reads stay masked. This deliberately named method is the sole
        repository boundary through which the router obtains raw credentials.
        """
        rows = self.connection.execute(
            """
            SELECT c.* FROM notification_channels AS c
            JOIN search_notification_channels AS sc ON sc.channel_id = c.id
            WHERE sc.search_id = ? AND c.enabled = 1
            ORDER BY c.name COLLATE NOCASE, c.id
            """,
            (search_id,),
        ).fetchall()
        return tuple(_delivery_channel_config(row) for row in rows)

    def delivery_channel(
        self, channel_id: int
    ) -> NotificationChannelDeliveryConfig:
        row = self.connection.execute(
            "SELECT * FROM notification_channels WHERE id = ?", (channel_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"notification channel {channel_id} does not exist")
        return _delivery_channel_config(row)

    def preferences_for_search(self, search_id: int) -> NotificationPreferences:
        if not self._search_exists(search_id):
            raise LookupError(f"search {search_id} does not exist")
        row = self.connection.execute(
            "SELECT * FROM search_notification_preferences WHERE search_id = ?",
            (search_id,),
        ).fetchone()
        if row is None:
            return NotificationPreferences()
        return _preferences_record(row)

    def set_event_preferences(
        self, search_id: int, preferences: NotificationPreferences
    ) -> NotificationPreferences:
        if not isinstance(preferences, NotificationPreferences):
            raise TypeError("preferences must be NotificationPreferences")
        if not self._search_exists(search_id):
            raise LookupError(f"search {search_id} does not exist")
        self.connection.execute(
            """
            INSERT INTO search_notification_preferences (
                search_id, notify_new_listing, notify_price_drop,
                notify_price_increase, notify_reappearance, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (search_id) DO UPDATE SET
                notify_new_listing = excluded.notify_new_listing,
                notify_price_drop = excluded.notify_price_drop,
                notify_price_increase = excluded.notify_price_increase,
                notify_reappearance = excluded.notify_reappearance,
                updated_at = excluded.updated_at
            """,
            (
                search_id,
                int(preferences.new_listing),
                int(preferences.price_drop),
                int(preferences.price_increase),
                int(preferences.reappearance),
                _utc_now(),
            ),
        )
        return self.preferences_for_search(search_id)

    def select_enabled_events(
        self,
        search_id: int,
        events: tuple[NotificationEventRecord, ...],
    ) -> tuple[NotificationEventRecord, ...]:
        preferences = self.preferences_for_search(search_id)
        for event in events:
            if event.search_id != search_id:
                raise ValueError("all events must originate from the selected search")
        return tuple(event for event in events if preferences.is_enabled(event.event_type))

    def create_event(
        self,
        search_id: int,
        event_type: NotificationEventType,
        deduplication_key: str,
        occurred_at: datetime,
        *,
        listing_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> EventCreationResult:
        cursor = self.connection.execute(
            """
            INSERT INTO notification_events (
                search_id, listing_id, event_type, deduplication_key,
                payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (deduplication_key) DO NOTHING
            """,
            (
                search_id,
                listing_id,
                event_type.value,
                deduplication_key,
                _json(payload or {}),
                _timestamp(occurred_at),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM notification_events WHERE deduplication_key = ?",
            (deduplication_key,),
        ).fetchone()
        return EventCreationResult(_event_record(row), bool(cursor.rowcount))

    def event_for_delivery(self, event_id: int) -> NotificationEvent:
        row = self.connection.execute(
            """
            SELECT e.*, s.name AS search_name,
                   l.portal_key, l.title AS listing_title, l.canonical_url,
                   l.price_euros AS listing_price_euros, l.area_sqm, l.rooms,
                   l.location, l.neighbourhood
            FROM notification_events AS e
            JOIN searches AS s ON s.id = e.search_id
            LEFT JOIN listings AS l ON l.id = e.listing_id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"notification event {event_id} does not exist")
        payload = json.loads(row["payload_json"])
        return NotificationEvent(
            id=row["id"],
            search_id=row["search_id"],
            search_name=row["search_name"],
            event_type=NotificationEventType(row["event_type"]),
            occurred_at=_parse_timestamp(row["occurred_at"]),
            listing_id=row["listing_id"],
            listing_title=row["listing_title"],
            portal=PortalKey(row["portal_key"]) if row["portal_key"] else None,
            canonical_url=row["canonical_url"],
            price_euros=_payload_integer(
                payload, "price_euros", row["listing_price_euros"]
            ),
            previous_price_euros=_payload_integer(
                payload, "previous_price_euros", None
            ),
            area_sqm=row["area_sqm"],
            rooms=row["rooms"],
            location=row["location"],
            neighbourhood=row["neighbourhood"],
        )

    def ensure_event_deliveries(
        self, event_id: int, *, available_at: datetime | None = None
    ) -> tuple[DeliveryAttemptRecord, ...]:
        event_row = self.connection.execute(
            "SELECT * FROM notification_events WHERE id = ?", (event_id,)
        ).fetchone()
        if event_row is None:
            raise LookupError(f"notification event {event_id} does not exist")
        search_id = event_row["search_id"]
        event_type = NotificationEventType(event_row["event_type"])
        if not self.preferences_for_search(search_id).is_enabled(event_type):
            return ()
        attempts = []
        for channel in self.delivery_channels_for_search(search_id):
            attempt, _ = self.ensure_delivery_attempt(
                event_id,
                channel.id,
                available_at=available_at,
            )
            attempts.append(attempt)
        return tuple(attempts)

    def ensure_delivery_attempt(
        self,
        event_id: int,
        channel_id: int,
        *,
        attempt_number: int = 1,
        available_at: datetime | None = None,
    ) -> tuple[DeliveryAttemptRecord, bool]:
        available = _timestamp(available_at or datetime.now(timezone.utc))
        cursor = self.connection.execute(
            """
            INSERT INTO notification_delivery_attempts (
                event_id, channel_id, attempt_number, available_at
            ) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
            """,
            (event_id, channel_id, attempt_number, available),
        )
        row = self.connection.execute(
            """
            SELECT * FROM notification_delivery_attempts
            WHERE event_id = ? AND channel_id = ? AND attempt_number = ?
            """,
            (event_id, channel_id, attempt_number),
        ).fetchone()
        return _delivery_record(row), bool(cursor.rowcount)

    def claim_next_delivery(
        self,
        now: datetime,
        *,
        lease_seconds: float = 60.0,
        claim_token: str | None = None,
    ) -> ClaimedDelivery | None:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, (int, float))
            or lease_seconds <= 0
            or lease_seconds > 3600
        ):
            raise ValueError("lease_seconds must be between 0 and 3600")
        current = _timestamp(now)
        lease_expires = _timestamp(now + timedelta(seconds=lease_seconds))
        token = claim_token or uuid.uuid4().hex
        if not token.strip():
            raise ValueError("claim_token must not be empty")
        with transaction(self.connection, immediate=True):
            row = self.connection.execute(
                """
                SELECT a.id
                FROM notification_delivery_attempts AS a
                JOIN notification_events AS e ON e.id = a.event_id
                JOIN notification_channels AS c ON c.id = a.channel_id
                JOIN search_notification_channels AS sc
                  ON sc.search_id = e.search_id AND sc.channel_id = a.channel_id
                WHERE c.enabled = 1
                  AND (
                    (a.status = 'pending' AND datetime(a.available_at) <= datetime(?))
                    OR (
                        a.status = 'claimed'
                        AND datetime(a.lease_expires_at) <= datetime(?)
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM notification_delivery_attempts AS succeeded
                    WHERE succeeded.event_id = a.event_id
                      AND succeeded.channel_id = a.channel_id
                      AND succeeded.status = 'succeeded'
                  )
                ORDER BY datetime(a.available_at), a.id
                LIMIT 1
                """,
                (current, current),
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                """
                UPDATE notification_delivery_attempts
                SET status = 'claimed', claimed_at = ?, completed_at = NULL,
                    error_category = NULL, redacted_diagnostic = NULL,
                    provider_message_id = NULL, claim_token = ?, lease_expires_at = ?
                WHERE id = ?
                """,
                (current, token, lease_expires, row["id"]),
            )
            claimed_row = self.connection.execute(
                "SELECT * FROM notification_delivery_attempts WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return ClaimedDelivery(_delivery_record(claimed_row), token)

    def complete_delivery(
        self,
        claim: ClaimedDelivery,
        *,
        success: bool,
        completed_at: datetime,
        error_category: str | None = None,
        diagnostic: str | None = None,
        provider_message_id: str | None = None,
        max_attempts: int = 3,
        base_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 900.0,
    ) -> DeliveryCompletion:
        _validate_retry_policy(max_attempts, base_backoff_seconds, max_backoff_seconds)
        completed = _timestamp(completed_at)
        if success:
            error_category = None
            diagnostic = None
        elif not error_category or not error_category.strip():
            raise ValueError("failed delivery requires an error category")
        diagnostic = _bounded_diagnostic(diagnostic)

        with transaction(self.connection, immediate=True):
            row = self.connection.execute(
                "SELECT * FROM notification_delivery_attempts WHERE id = ?",
                (claim.attempt.id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != DeliveryStatus.CLAIMED.value
                or row["claim_token"] != claim.claim_token
            ):
                raise StaleDeliveryClaimError("delivery claim is stale or no longer owned")
            status = DeliveryStatus.SUCCEEDED if success else DeliveryStatus.FAILED
            self.connection.execute(
                """
                UPDATE notification_delivery_attempts
                SET status = ?, completed_at = ?, error_category = ?,
                    redacted_diagnostic = ?, provider_message_id = ?,
                    claim_token = NULL, lease_expires_at = NULL
                WHERE id = ?
                """,
                (
                    status.value,
                    completed,
                    error_category.strip() if error_category else None,
                    diagnostic,
                    provider_message_id,
                    row["id"],
                ),
            )
            retry = None
            if not success and row["attempt_number"] < max_attempts:
                delay = min(
                    max_backoff_seconds,
                    base_backoff_seconds * (2 ** (row["attempt_number"] - 1)),
                )
                retry_at = completed_at + timedelta(seconds=delay)
                retry, _ = self.ensure_delivery_attempt(
                    row["event_id"],
                    row["channel_id"],
                    attempt_number=row["attempt_number"] + 1,
                    available_at=retry_at,
                )
            completed_row = self.connection.execute(
                "SELECT * FROM notification_delivery_attempts WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return DeliveryCompletion(_delivery_record(completed_row), retry)

    def create_retry(
        self, event_id: int, channel_id: int
    ) -> DeliveryAttemptRecord:
        with transaction(self.connection, immediate=True):
            next_number = self.connection.execute(
                """
                SELECT coalesce(max(attempt_number), 0) + 1
                FROM notification_delivery_attempts
                WHERE event_id = ? AND channel_id = ?
                """,
                (event_id, channel_id),
            ).fetchone()[0]
            attempt, _ = self.ensure_delivery_attempt(
                event_id, channel_id, attempt_number=next_number
            )
        return attempt

    def _search_exists(self, search_id: int) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM searches WHERE id = ?", (search_id,)
            ).fetchone()
            is not None
        )


def _channel_record(row: sqlite3.Row) -> NotificationChannelRecord:
    return NotificationChannelRecord(
        id=row["id"],
        name=row["name"],
        provider=NotificationProvider(row["provider"]),
        config=json.loads(row["config_json"]),
        secret_config=_mask_secrets(json.loads(row["secret_config_json"])),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_record(row: sqlite3.Row) -> NotificationEventRecord:
    return NotificationEventRecord(
        id=row["id"],
        search_id=row["search_id"],
        listing_id=row["listing_id"],
        event_type=NotificationEventType(row["event_type"]),
        deduplication_key=row["deduplication_key"],
        payload=json.loads(row["payload_json"]),
        occurred_at=row["occurred_at"],
        created_at=row["created_at"],
    )


def _delivery_channel_config(
    row: sqlite3.Row,
) -> NotificationChannelDeliveryConfig:
    return NotificationChannelDeliveryConfig(
        id=row["id"],
        name=row["name"],
        provider=NotificationProvider(row["provider"]),
        config=json.loads(row["config_json"]),
        secret_config=json.loads(row["secret_config_json"]),
    )


def _delivery_record(row: sqlite3.Row) -> DeliveryAttemptRecord:
    return DeliveryAttemptRecord(
        id=row["id"],
        event_id=row["event_id"],
        channel_id=row["channel_id"],
        attempt_number=row["attempt_number"],
        status=DeliveryStatus(row["status"]),
        claimed_at=row["claimed_at"],
        completed_at=row["completed_at"],
        error_category=row["error_category"],
        redacted_diagnostic=row["redacted_diagnostic"],
        provider_message_id=row["provider_message_id"],
        created_at=row["created_at"],
        available_at=row["available_at"],
        lease_expires_at=row["lease_expires_at"],
    )


def _channel_test_record(row: sqlite3.Row) -> NotificationChannelTestRecord:
    return NotificationChannelTestRecord(
        id=row["id"],
        channel_id=row["channel_id"],
        success=bool(row["success"]),
        error_category=row["error_category"],
        redacted_diagnostic=row["redacted_diagnostic"],
        tested_at=row["tested_at"],
    )


def _preferences_record(row: sqlite3.Row) -> NotificationPreferences:
    return NotificationPreferences(
        new_listing=bool(row["notify_new_listing"]),
        price_drop=bool(row["notify_price_drop"]),
        price_increase=bool(row["notify_price_increase"]),
        reappearance=bool(row["notify_reappearance"]),
    )


def _mask_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _mask_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_secrets(item) for item in value]
    if value in (None, ""):
        return value
    return MASKED_SECRET


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _payload_integer(payload: dict[str, Any], key: str, fallback: Any) -> int | None:
    value = payload.get(key, fallback)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return fallback
    return value


def _bounded_diagnostic(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned[:2000] or None


def _validate_retry_policy(
    max_attempts: int,
    base_backoff_seconds: float,
    max_backoff_seconds: float,
) -> None:
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 10
    ):
        raise ValueError("max_attempts must be between 1 and 10")
    for value in (base_backoff_seconds, max_backoff_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("retry backoff values must be numeric")
    if base_backoff_seconds <= 0 or max_backoff_seconds <= 0:
        raise ValueError("retry backoff values must be positive")
    if base_backoff_seconds > max_backoff_seconds:
        raise ValueError("base backoff cannot exceed maximum backoff")
