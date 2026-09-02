"""Normalized domain types shared by scraping, persistence, and services."""

from scrapyrealestate.domain.capabilities import (
    CapabilityReport,
    FilterCapabilities,
    FilterSupport,
    SearchFilterKey,
    active_filter_keys,
    report_capabilities,
)
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
    "CapabilityReport",
    "FilterCapabilities",
    "FilterEvaluation",
    "FilterOutcome",
    "FilterSupport",
    "LegacyItemMappingError",
    "NormalizedListing",
    "NormalizedSearch",
    "PortalKey",
    "PropertyType",
    "RunStatus",
    "SearchFilterKey",
    "SearchFilters",
    "TransactionType",
    "TriState",
    "active_filter_keys",
    "canonicalize_url",
    "evaluate_filters",
    "evaluate_listing",
    "map_legacy_item",
    "normalize_area_sqm",
    "normalize_count",
    "normalize_euro_price",
    "normalize_floor",
    "normalize_nullable_boolean",
    "report_capabilities",
]
