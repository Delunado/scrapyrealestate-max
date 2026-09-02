"""Repositories for notification channels, events, and delivery attempts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from scrapyrealestate.domain.notification import (
    NotificationEventType,
    NotificationPreferences,
)
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

    def ensure_delivery_attempt(
        self, event_id: int, channel_id: int, *, attempt_number: int = 1
    ) -> tuple[DeliveryAttemptRecord, bool]:
        cursor = self.connection.execute(
            """
            INSERT INTO notification_delivery_attempts (
                event_id, channel_id, attempt_number
            ) VALUES (?, ?, ?) ON CONFLICT DO NOTHING
            """,
            (event_id, channel_id, attempt_number),
        )
        row = self.connection.execute(
            """
            SELECT * FROM notification_delivery_attempts
            WHERE event_id = ? AND channel_id = ? AND attempt_number = ?
            """,
            (event_id, channel_id, attempt_number),
        ).fetchone()
        return _delivery_record(row), bool(cursor.rowcount)

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
