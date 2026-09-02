"""Portal-independent listing model and identity rules."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from scrapyrealestate.domain.values import (
    PortalKey,
    PropertyType,
    TransactionType,
    TriState,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonicalize_url(value: str) -> str:
    """Return a stable absolute HTTP(S) URL without credentials or fragments."""
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as error:
        raise ValueError("canonical_url is not a valid URL") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("canonical_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("canonical_url must not contain credentials")

    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}
    netloc = host if port is None or default_port else f"{host}:{port}"
    normalized = SplitResult(
        parsed.scheme.lower(),
        netloc,
        parsed.path or "/",
        parsed.query,
        "",
    )
    return urlunsplit(normalized)


def _utc_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class NormalizedListing:
    """A validated listing at the spider/adapter boundary.

    ``portal`` plus ``external_id`` is the preferred identity. A canonical source
    URL is the fallback when a portal does not expose a stable identifier.
    """

    portal: PortalKey
    transaction_type: TransactionType
    title: str
    external_id: str | None = None
    canonical_url: str | None = None
    property_type: PropertyType = PropertyType.UNKNOWN
    price_euros: int | None = None
    area_sqm: float | None = None
    rooms: int | None = None
    bathrooms: int | None = None
    floor: int | None = None
    elevator: TriState = TriState.UNKNOWN
    terrace: TriState = TriState.UNKNOWN
    garage: TriState = TriState.UNKNOWN
    location: str | None = None
    neighbourhood: str | None = None
    street: str | None = None
    street_number: str | None = None
    posted_at: datetime | None = None
    observed_at: datetime = field(default_factory=utc_now)
    raw_source: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.portal, PortalKey):
            raise TypeError("portal must be a PortalKey")
        if not isinstance(self.transaction_type, TransactionType):
            raise TypeError("transaction_type must be a TransactionType")

        title = self.title.strip()
        if not title:
            raise ValueError("title is required")
        object.__setattr__(self, "title", title)

        external_id = _optional_text(self.external_id)
        canonical_url = (
            canonicalize_url(self.canonical_url) if _optional_text(self.canonical_url) else None
        )
        if external_id is None and canonical_url is None:
            raise ValueError("external_id or canonical_url is required")
        object.__setattr__(self, "external_id", external_id)
        object.__setattr__(self, "canonical_url", canonical_url)

        for field_name in ("price_euros", "rooms", "bathrooms"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")
        if self.area_sqm is not None and self.area_sqm <= 0:
            raise ValueError("area_sqm must be positive")

        for field_name in ("location", "neighbourhood", "street", "street_number"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))

        object.__setattr__(self, "observed_at", _utc_datetime(self.observed_at, "observed_at"))
        if self.posted_at is not None:
            object.__setattr__(self, "posted_at", _utc_datetime(self.posted_at, "posted_at"))
        if not isinstance(self.raw_source, Mapping):
            raise TypeError("raw_source must be a mapping")
        object.__setattr__(self, "raw_source", MappingProxyType(dict(self.raw_source)))

    @property
    def identity(self) -> tuple[PortalKey, str]:
        return self.portal, self.external_id or self.canonical_url or ""

    @property
    def price_per_sqm(self) -> float | None:
        if self.price_euros is None or self.area_sqm is None:
            return None
        return self.price_euros / self.area_sqm

    def to_dict(self) -> dict[str, Any]:
        """Serialize to primitives suitable for JSON and later persistence."""
        return {
            "portal": self.portal.value,
            "transaction_type": self.transaction_type.value,
            "title": self.title,
            "external_id": self.external_id,
            "canonical_url": self.canonical_url,
            "property_type": self.property_type.value,
            "price_euros": self.price_euros,
            "area_sqm": self.area_sqm,
            "rooms": self.rooms,
            "bathrooms": self.bathrooms,
            "floor": self.floor,
            "elevator": self.elevator.value,
            "terrace": self.terrace.value,
            "garage": self.garage.value,
            "location": self.location,
            "neighbourhood": self.neighbourhood,
            "street": self.street,
            "street_number": self.street_number,
            "posted_at": _serialize_datetime(self.posted_at),
            "observed_at": _serialize_datetime(self.observed_at),
            "raw_source": dict(self.raw_source),
        }


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
