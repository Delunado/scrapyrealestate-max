"""Normalized saved-search and local-filter models."""

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

from scrapyrealestate.domain.values import PropertyType, TransactionType


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Portal-independent constraints; ``None`` means no constraint."""

    min_price_euros: int | None = None
    max_price_euros: int | None = None
    min_area_sqm: float | None = None
    max_area_sqm: float | None = None
    min_rooms: int | None = None
    max_rooms: int | None = None
    min_bathrooms: int | None = None
    max_bathrooms: int | None = None
    location: str | None = None
    neighbourhood: str | None = None
    min_floor: int | None = None
    max_floor: int | None = None
    elevator: bool | None = None
    terrace: bool | None = None
    garage: bool | None = None
    property_types: frozenset[PropertyType] = field(default_factory=frozenset)
    max_price_per_sqm: float | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "min_price_euros",
            "max_price_euros",
            "min_rooms",
            "max_rooms",
            "min_bathrooms",
            "max_bathrooms",
        ):
            value = getattr(self, field_name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError(f"{field_name} must be an integer or None")
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")

        for field_name in ("min_area_sqm", "max_area_sqm", "max_price_per_sqm"):
            value = getattr(self, field_name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise TypeError(f"{field_name} must be numeric or None")
            if value is not None and (not isfinite(value) or value <= 0):
                raise ValueError(f"{field_name} must be positive")

        for field_name in ("min_floor", "max_floor"):
            value = getattr(self, field_name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError(f"{field_name} must be an integer or None")
        for field_name in ("elevator", "terrace", "garage"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{field_name} must be a boolean or None")

        property_types = frozenset(self.property_types)
        if any(not isinstance(value, PropertyType) for value in property_types):
            raise TypeError("property_types must contain only PropertyType values")
        object.__setattr__(self, "property_types", property_types)
        object.__setattr__(self, "location", _optional_text(self.location))
        object.__setattr__(self, "neighbourhood", _optional_text(self.neighbourhood))

        for low_name, high_name in (
            ("min_price_euros", "max_price_euros"),
            ("min_area_sqm", "max_area_sqm"),
            ("min_rooms", "max_rooms"),
            ("min_bathrooms", "max_bathrooms"),
            ("min_floor", "max_floor"),
        ):
            low, high = getattr(self, low_name), getattr(self, high_name)
            if low is not None and high is not None and low > high:
                raise ValueError(f"{low_name} cannot exceed {high_name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_price_euros": self.min_price_euros,
            "max_price_euros": self.max_price_euros,
            "min_area_sqm": self.min_area_sqm,
            "max_area_sqm": self.max_area_sqm,
            "min_rooms": self.min_rooms,
            "max_rooms": self.max_rooms,
            "min_bathrooms": self.min_bathrooms,
            "max_bathrooms": self.max_bathrooms,
            "location": self.location,
            "neighbourhood": self.neighbourhood,
            "min_floor": self.min_floor,
            "max_floor": self.max_floor,
            "elevator": self.elevator,
            "terrace": self.terrace,
            "garage": self.garage,
            "property_types": sorted(value.value for value in self.property_types),
            "max_price_per_sqm": self.max_price_per_sqm,
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "SearchFilters":
        data = dict(values)
        data["property_types"] = frozenset(
            PropertyType(value) for value in data.get("property_types", ())
        )
        return cls(**data)


@dataclass(frozen=True, slots=True)
class NormalizedSearch:
    """User intent independent of any portal-specific request format."""

    name: str
    transaction_type: TransactionType
    filters: SearchFilters = field(default_factory=SearchFilters)

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("search name is required")
        if not isinstance(self.transaction_type, TransactionType):
            raise TypeError("transaction_type must be a TransactionType")
        if not isinstance(self.filters, SearchFilters):
            raise TypeError("filters must be SearchFilters")
        object.__setattr__(self, "name", name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transaction_type": self.transaction_type.value,
            "filters": self.filters.to_dict(),
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "NormalizedSearch":
        return cls(
            name=values["name"],
            transaction_type=TransactionType(values["transaction_type"]),
            filters=SearchFilters.from_dict(values.get("filters", {})),
        )
