"""Portal filter capability metadata."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from scrapyrealestate.domain.search import SearchFilters


class SearchFilterKey(StrEnum):
    """Stable names for every constraint in :class:`SearchFilters`."""

    MIN_PRICE_EUROS = "min_price_euros"
    MAX_PRICE_EUROS = "max_price_euros"
    MIN_AREA_SQM = "min_area_sqm"
    MAX_AREA_SQM = "max_area_sqm"
    MIN_ROOMS = "min_rooms"
    MAX_ROOMS = "max_rooms"
    MIN_BATHROOMS = "min_bathrooms"
    MAX_BATHROOMS = "max_bathrooms"
    LOCATION = "location"
    NEIGHBOURHOOD = "neighbourhood"
    MIN_FLOOR = "min_floor"
    MAX_FLOOR = "max_floor"
    ELEVATOR = "elevator"
    TERRACE = "terrace"
    GARAGE = "garage"
    PROPERTY_TYPES = "property_types"
    MAX_PRICE_PER_SQM = "max_price_per_sqm"


class FilterSupport(StrEnum):
    """Where a portal adapter can apply a normalized search filter."""

    REMOTE = "remote"
    LOCAL = "local"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class FilterCapabilities:
    """Complete, non-overlapping filter support declared by one portal.

    Remote filters are encoded into a portal request, local filters are evaluated
    after normalization, and unsupported filters cannot be evaluated reliably.
    Every normalized filter must be classified so callers cannot silently ignore
    constraints added to a search.
    """

    remote: frozenset[SearchFilterKey] = field(default_factory=frozenset)
    local: frozenset[SearchFilterKey] = field(default_factory=frozenset)
    unsupported: frozenset[SearchFilterKey] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        groups = {
            FilterSupport.REMOTE: frozenset(self.remote),
            FilterSupport.LOCAL: frozenset(self.local),
            FilterSupport.UNSUPPORTED: frozenset(self.unsupported),
        }
        for support, keys in groups.items():
            if any(not isinstance(key, SearchFilterKey) for key in keys):
                raise TypeError(f"{support.value} must contain only SearchFilterKey values")

        classified = set().union(*groups.values())
        duplicates = sum(len(keys) for keys in groups.values()) - len(classified)
        if duplicates:
            raise ValueError("each filter capability must have exactly one classification")

        missing = set(SearchFilterKey) - classified
        if missing:
            names = ", ".join(sorted(key.value for key in missing))
            raise ValueError(f"missing filter capabilities: {names}")

        object.__setattr__(self, "remote", groups[FilterSupport.REMOTE])
        object.__setattr__(self, "local", groups[FilterSupport.LOCAL])
        object.__setattr__(self, "unsupported", groups[FilterSupport.UNSUPPORTED])

    def support_for(self, key: SearchFilterKey) -> FilterSupport:
        """Return the declared support for one normalized filter."""
        if not isinstance(key, SearchFilterKey):
            raise TypeError("key must be a SearchFilterKey")
        if key in self.remote:
            return FilterSupport.REMOTE
        if key in self.local:
            return FilterSupport.LOCAL
        return FilterSupport.UNSUPPORTED

    def to_dict(self) -> dict[str, list[str]]:
        return {
            support.value: sorted(key.value for key in getattr(self, support.value))
            for support in FilterSupport
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "FilterCapabilities":
        unknown = set(values) - {support.value for support in FilterSupport}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown filter capability groups: {names}")
        return cls(
            **{
                support.value: frozenset(
                    SearchFilterKey(value) for value in values.get(support.value, ())
                )
                for support in FilterSupport
            }
        )


def active_filter_keys(filters: SearchFilters) -> frozenset[SearchFilterKey]:
    """Return every filter ``filters`` actually constrains.

    ``SearchFilters`` uses ``None`` (or an empty ``property_types``) for "no
    constraint"; every other value, including ``False`` for a boolean
    amenity, is a real request that some portal must classify.
    """
    single_valued = {
        SearchFilterKey.MIN_PRICE_EUROS: filters.min_price_euros,
        SearchFilterKey.MAX_PRICE_EUROS: filters.max_price_euros,
        SearchFilterKey.MIN_AREA_SQM: filters.min_area_sqm,
        SearchFilterKey.MAX_AREA_SQM: filters.max_area_sqm,
        SearchFilterKey.MIN_ROOMS: filters.min_rooms,
        SearchFilterKey.MAX_ROOMS: filters.max_rooms,
        SearchFilterKey.MIN_BATHROOMS: filters.min_bathrooms,
        SearchFilterKey.MAX_BATHROOMS: filters.max_bathrooms,
        SearchFilterKey.LOCATION: filters.location,
        SearchFilterKey.NEIGHBOURHOOD: filters.neighbourhood,
        SearchFilterKey.MIN_FLOOR: filters.min_floor,
        SearchFilterKey.MAX_FLOOR: filters.max_floor,
        SearchFilterKey.ELEVATOR: filters.elevator,
        SearchFilterKey.TERRACE: filters.terrace,
        SearchFilterKey.GARAGE: filters.garage,
        SearchFilterKey.MAX_PRICE_PER_SQM: filters.max_price_per_sqm,
    }
    active = {key for key, value in single_valued.items() if value is not None}
    if filters.property_types:
        active.add(SearchFilterKey.PROPERTY_TYPES)
    return frozenset(active)


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """Where each filter a search actually requests will be evaluated.

    Unlike :class:`FilterCapabilities`, which classifies every possible
    filter a portal could ever be asked for, a report only classifies the
    filters one particular search requests, so a caller can render or log
    exactly what will happen to that search without guessing from silence.
    """

    remote: frozenset[SearchFilterKey] = field(default_factory=frozenset)
    local: frozenset[SearchFilterKey] = field(default_factory=frozenset)
    unsupported: frozenset[SearchFilterKey] = field(default_factory=frozenset)

    @property
    def has_unsupported(self) -> bool:
        return bool(self.unsupported)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            support.value: sorted(key.value for key in getattr(self, support.value))
            for support in FilterSupport
        }


def report_capabilities(
    filters: SearchFilters, capabilities: FilterCapabilities
) -> CapabilityReport:
    """Classify only the filters ``filters`` requests against ``capabilities``.

    A filter ``filters`` leaves unset is omitted entirely rather than being
    implied remote, local, or unsupported.
    """
    if not isinstance(capabilities, FilterCapabilities):
        raise TypeError("capabilities must be FilterCapabilities")
    groups: dict[FilterSupport, set[SearchFilterKey]] = {
        support: set() for support in FilterSupport
    }
    for key in active_filter_keys(filters):
        groups[capabilities.support_for(key)].add(key)
    return CapabilityReport(
        remote=frozenset(groups[FilterSupport.REMOTE]),
        local=frozenset(groups[FilterSupport.LOCAL]),
        unsupported=frozenset(groups[FilterSupport.UNSUPPORTED]),
    )
