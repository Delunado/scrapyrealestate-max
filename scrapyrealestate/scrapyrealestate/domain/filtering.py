"""Deterministic local evaluation of normalized search filters."""

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PropertyType, TriState


class FilterOutcome(StrEnum):
    MATCH = "match"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FilterEvaluation:
    """Aggregate result plus auditable per-filter outcomes."""

    outcome: FilterOutcome
    checks: Mapping[str, FilterOutcome]

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))

    @property
    def failed_filters(self) -> tuple[str, ...]:
        return tuple(name for name, outcome in self.checks.items() if outcome is FilterOutcome.NO_MATCH)

    @property
    def unknown_filters(self) -> tuple[str, ...]:
        return tuple(name for name, outcome in self.checks.items() if outcome is FilterOutcome.UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "checks": {name: outcome.value for name, outcome in self.checks.items()},
        }


def evaluate_listing(listing: NormalizedListing, search: NormalizedSearch) -> FilterEvaluation:
    """Evaluate transaction type and every active local search filter."""
    checks = {
        "transaction_type": (
            FilterOutcome.MATCH
            if listing.transaction_type is search.transaction_type
            else FilterOutcome.NO_MATCH
        )
    }
    checks.update(_filter_checks(listing, search.filters))
    return _evaluation(checks)


def evaluate_filters(listing: NormalizedListing, filters: SearchFilters) -> FilterEvaluation:
    """Evaluate filter constraints when transaction type is handled elsewhere."""
    return _evaluation(_filter_checks(listing, filters))


def _filter_checks(
    listing: NormalizedListing, filters: SearchFilters
) -> dict[str, FilterOutcome]:
    checks: dict[str, FilterOutcome] = {}
    _range_checks(checks, "price_euros", listing.price_euros, filters.min_price_euros, filters.max_price_euros)
    _range_checks(checks, "area_sqm", listing.area_sqm, filters.min_area_sqm, filters.max_area_sqm)
    _range_checks(checks, "rooms", listing.rooms, filters.min_rooms, filters.max_rooms)
    _range_checks(
        checks,
        "bathrooms",
        listing.bathrooms,
        filters.min_bathrooms,
        filters.max_bathrooms,
    )
    _range_checks(checks, "floor", listing.floor, filters.min_floor, filters.max_floor)

    if filters.location is not None:
        checks["location"] = _text_match(listing.location, filters.location)
    if filters.neighbourhood is not None:
        checks["neighbourhood"] = _text_match(
            listing.neighbourhood, filters.neighbourhood
        )
    for field_name in ("elevator", "terrace", "garage"):
        expected = getattr(filters, field_name)
        if expected is not None:
            checks[field_name] = _amenity_match(getattr(listing, field_name), expected)
    if filters.property_types:
        if listing.property_type is PropertyType.UNKNOWN:
            checks["property_type"] = FilterOutcome.UNKNOWN
        else:
            checks["property_type"] = (
                FilterOutcome.MATCH
                if listing.property_type in filters.property_types
                else FilterOutcome.NO_MATCH
            )
    if filters.max_price_per_sqm is not None:
        checks["max_price_per_sqm"] = _maximum(
            listing.price_per_sqm, filters.max_price_per_sqm
        )
    return checks


def _range_checks(
    checks: dict[str, FilterOutcome],
    name: str,
    actual: int | float | None,
    minimum: int | float | None,
    maximum: int | float | None,
) -> None:
    if minimum is not None:
        checks[f"min_{name}"] = _minimum(actual, minimum)
    if maximum is not None:
        checks[f"max_{name}"] = _maximum(actual, maximum)


def _minimum(actual: int | float | None, expected: int | float) -> FilterOutcome:
    if actual is None:
        return FilterOutcome.UNKNOWN
    return FilterOutcome.MATCH if actual >= expected else FilterOutcome.NO_MATCH


def _maximum(actual: int | float | None, expected: int | float) -> FilterOutcome:
    if actual is None:
        return FilterOutcome.UNKNOWN
    return FilterOutcome.MATCH if actual <= expected else FilterOutcome.NO_MATCH


def _text_match(actual: str | None, expected: str) -> FilterOutcome:
    if actual is None:
        return FilterOutcome.UNKNOWN
    return FilterOutcome.MATCH if _fold(actual) == _fold(expected) else FilterOutcome.NO_MATCH


def _amenity_match(actual: TriState, expected: bool) -> FilterOutcome:
    if actual is TriState.UNKNOWN:
        return FilterOutcome.UNKNOWN
    actual_bool = actual is TriState.YES
    return FilterOutcome.MATCH if actual_bool is expected else FilterOutcome.NO_MATCH


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(unaccented.casefold().split())


def _evaluation(checks: dict[str, FilterOutcome]) -> FilterEvaluation:
    outcomes = checks.values()
    if FilterOutcome.NO_MATCH in outcomes:
        outcome = FilterOutcome.NO_MATCH
    elif FilterOutcome.UNKNOWN in outcomes:
        outcome = FilterOutcome.UNKNOWN
    else:
        outcome = FilterOutcome.MATCH
    return FilterEvaluation(outcome=outcome, checks=checks)
