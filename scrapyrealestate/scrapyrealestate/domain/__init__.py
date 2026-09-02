"""Normalized domain types shared by scraping, persistence, and services."""

from scrapyrealestate.domain.filtering import (
    FilterEvaluation,
    FilterOutcome,
    evaluate_filters,
    evaluate_listing,
)
from scrapyrealestate.domain.listing import NormalizedListing, canonicalize_url
from scrapyrealestate.domain.legacy_mapper import LegacyItemMappingError, map_legacy_item
from scrapyrealestate.domain.normalization import (
    normalize_area_sqm,
    normalize_count,
    normalize_euro_price,
    normalize_floor,
    normalize_nullable_boolean,
)
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters

from scrapyrealestate.domain.values import (
    PortalKey,
    PropertyType,
    RunStatus,
    TransactionType,
    TriState,
)

__all__ = [
    "FilterEvaluation",
    "FilterOutcome",
    "LegacyItemMappingError",
    "NormalizedListing",
    "NormalizedSearch",
    "PortalKey",
    "PropertyType",
    "RunStatus",
    "SearchFilters",
    "TransactionType",
    "TriState",
    "canonicalize_url",
    "evaluate_filters",
    "evaluate_listing",
    "map_legacy_item",
    "normalize_area_sqm",
    "normalize_count",
    "normalize_euro_price",
    "normalize_floor",
    "normalize_nullable_boolean",
]
