"""Normalized domain types shared by scraping, persistence, and services."""

from scrapyrealestate.domain.listing import NormalizedListing, canonicalize_url
from scrapyrealestate.domain.legacy_mapper import LegacyItemMappingError, map_legacy_item
from scrapyrealestate.domain.normalization import (
    normalize_area_sqm,
    normalize_count,
    normalize_euro_price,
    normalize_floor,
    normalize_nullable_boolean,
)

from scrapyrealestate.domain.values import (
    PortalKey,
    PropertyType,
    RunStatus,
    TransactionType,
    TriState,
)

__all__ = [
    "LegacyItemMappingError",
    "NormalizedListing",
    "PortalKey",
    "PropertyType",
    "RunStatus",
    "TransactionType",
    "TriState",
    "canonicalize_url",
    "map_legacy_item",
    "normalize_area_sqm",
    "normalize_count",
    "normalize_euro_price",
    "normalize_floor",
    "normalize_nullable_boolean",
]
