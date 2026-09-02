"""Provider-neutral notification events exposed to delivery adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from math import isfinite

from scrapyrealestate.domain.listing import canonicalize_url
from scrapyrealestate.domain.values import PortalKey


class NotificationEventType(StrEnum):
    NEW_LISTING = "new_listing"
    PRICE_DROP = "price_drop"
    PRICE_INCREASE = "price_increase"
    REAPPEARANCE = "reappearance"


DEFAULT_ENABLED_EVENT_TYPES = frozenset(
    {
        NotificationEventType.NEW_LISTING,
        NotificationEventType.PRICE_DROP,
    }
)


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    """Per-search event selection, with conservative default noise levels."""

    new_listing: bool = True
    price_drop: bool = True
    price_increase: bool = False
    reappearance: bool = False

    def is_enabled(self, event_type: NotificationEventType) -> bool:
        if not isinstance(event_type, NotificationEventType):
            raise TypeError("event_type must be a NotificationEventType")
        return bool(getattr(self, event_type.value))

    @property
    def enabled_event_types(self) -> frozenset[NotificationEventType]:
        return frozenset(event_type for event_type in NotificationEventType if self.is_enabled(event_type))

    def to_dict(self) -> dict[str, bool]:
        return {event_type.value: self.is_enabled(event_type) for event_type in NotificationEventType}


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """Complete, provider-independent input for one notification delivery.

    This is a read model rather than a persistence record: routing code may
    construct it by joining an event to its search and listing. Listing fields
    are nullable so a persisted event remains deliverable if its listing is gone.
    """

    id: int
    search_id: int
    search_name: str
    event_type: NotificationEventType
    occurred_at: datetime
    listing_id: int | None = None
    listing_title: str | None = None
    portal: PortalKey | None = None
    canonical_url: str | None = None
    price_euros: int | None = None
    previous_price_euros: int | None = None
    area_sqm: float | None = None
    rooms: int | None = None
    location: str | None = None
    neighbourhood: str | None = None

    def __post_init__(self) -> None:
        if self.id <= 0 or self.search_id <= 0:
            raise ValueError("event and search IDs must be positive")
        if not isinstance(self.event_type, NotificationEventType):
            raise TypeError("event_type must be a NotificationEventType")
        if self.portal is not None and not isinstance(self.portal, PortalKey):
            raise TypeError("portal must be a PortalKey or None")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(timezone.utc))

        object.__setattr__(self, "search_name", _required_text(self.search_name, "search_name"))
        for field_name in ("listing_title", "location", "neighbourhood"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))
        if self.canonical_url is not None:
            object.__setattr__(self, "canonical_url", canonicalize_url(self.canonical_url))

        for field_name in ("price_euros", "previous_price_euros", "rooms"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer or None")
        if self.area_sqm is not None and (
            isinstance(self.area_sqm, bool)
            or not isinstance(self.area_sqm, (int, float))
            or not isfinite(self.area_sqm)
            or self.area_sqm <= 0
        ):
            raise ValueError("area_sqm must be a positive finite number or None")


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
