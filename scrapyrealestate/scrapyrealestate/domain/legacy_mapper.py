"""Compatibility mapping from the current loose Scrapy item contract."""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from scrapyrealestate.domain.listing import NormalizedListing, canonicalize_url, utc_now
from scrapyrealestate.domain.normalization import (
    normalize_area_sqm,
    normalize_count,
    normalize_euro_price,
    normalize_floor,
)
from scrapyrealestate.domain.values import PortalKey, PropertyType, TransactionType


class LegacyItemMappingError(ValueError):
    """A legacy item cannot satisfy the normalized listing contract."""


_TRANSACTION_ALIASES = {
    "buy": TransactionType.BUY,
    "sale": TransactionType.BUY,
    "venta": TransactionType.BUY,
    "comprar": TransactionType.BUY,
    "rent": TransactionType.RENT,
    "rental": TransactionType.RENT,
    "alquiler": TransactionType.RENT,
}

_PROPERTY_TITLE_MARKERS = (
    (PropertyType.GARAGE, ("garaje", "parking")),
    (PropertyType.STORAGE, ("trastero",)),
    (PropertyType.OFFICE, ("oficina",)),
    (PropertyType.COMMERCIAL, ("local", "nave")),
    (PropertyType.LAND, ("terreno", "parcela", "solar")),
    (PropertyType.BUILDING, ("edificio",)),
    (PropertyType.HOUSE, ("casa", "chalet", "adosado", "villa")),
    (PropertyType.APARTMENT, ("piso", "apartamento", "atico", "ático", "duplex", "dúplex", "estudio")),
)


def map_legacy_item(
    item: Mapping[str, Any],
    *,
    portal: PortalKey | None = None,
    observed_at: datetime | None = None,
) -> NormalizedListing:
    """Map all fields in ``ScrapyrealestateItem`` to a normalized listing.

    The original field values remain available in ``raw_source`` for parser and
    normalization diagnostics during the transition.
    """
    raw = dict(item)
    resolved_portal = portal or _portal_key(raw.get("site"))
    transaction_type = _transaction_type(raw.get("type"))
    external_id = _text(raw.get("id"))
    canonical_url = _safe_canonical_url(raw.get("href"))
    if external_id is None and canonical_url is None:
        raise LegacyItemMappingError("legacy item has no usable external ID or URL")

    title = _text(raw.get("title"))
    if title is None:
        raise LegacyItemMappingError("legacy item has no title")

    return NormalizedListing(
        portal=resolved_portal,
        transaction_type=transaction_type,
        title=title,
        external_id=external_id,
        canonical_url=canonical_url,
        property_type=_property_type(title),
        price_euros=normalize_euro_price(raw.get("price")),
        area_sqm=normalize_area_sqm(raw.get("m2")),
        rooms=normalize_count(raw.get("rooms")),
        floor=normalize_floor(raw.get("floor")),
        location=_text(raw.get("town")),
        neighbourhood=_text(raw.get("neighbour")),
        street=_text(raw.get("street")),
        street_number=_text(raw.get("number")),
        posted_at=_posted_at(raw.get("post_time")),
        observed_at=observed_at or utc_now(),
        raw_source=raw,
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _portal_key(value: Any) -> PortalKey:
    text = _text(value)
    try:
        return PortalKey(text)
    except (TypeError, ValueError) as error:
        raise LegacyItemMappingError(f"unknown legacy portal: {text!r}") from error


def _transaction_type(value: Any) -> TransactionType:
    text = _text(value)
    transaction_type = _TRANSACTION_ALIASES.get(text.casefold() if text else "")
    if transaction_type is None:
        raise LegacyItemMappingError(f"unknown legacy transaction type: {text!r}")
    return transaction_type


def _safe_canonical_url(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return canonicalize_url(text)
    except ValueError:
        return None


def _posted_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _property_type(title: str) -> PropertyType:
    folded = title.casefold()
    for property_type, markers in _PROPERTY_TITLE_MARKERS:
        if any(marker in folded for marker in markers):
            return property_type
    return PropertyType.UNKNOWN
