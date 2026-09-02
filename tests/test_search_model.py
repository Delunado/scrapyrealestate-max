import json

import pytest

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PropertyType, TransactionType


def test_search_model_covers_normalized_filter_fields_and_round_trips_json():
    search = NormalizedSearch(
        name="  Madrid centro  ",
        transaction_type=TransactionType.BUY,
        filters=SearchFilters(
            min_price_euros=100_000,
            max_price_euros=450_000,
            min_area_sqm=60,
            max_area_sqm=140,
            min_rooms=2,
            max_rooms=4,
            min_bathrooms=1,
            max_bathrooms=2,
            location=" Madrid ",
            neighbourhood=" Centro ",
            min_floor=1,
            max_floor=5,
            elevator=True,
            terrace=False,
            garage=True,
            property_types=frozenset({PropertyType.APARTMENT, PropertyType.HOUSE}),
            max_price_per_sqm=5_000,
        ),
    )

    serialized = json.loads(json.dumps(search.to_dict()))

    assert search.name == "Madrid centro"
    assert search.filters.location == "Madrid"
    assert serialized["filters"]["property_types"] == ["apartment", "house"]
    assert NormalizedSearch.from_dict(serialized) == search


def test_empty_filter_model_has_no_constraints():
    filters = SearchFilters()

    assert filters.property_types == frozenset()
    assert all(value in (None, []) for value in filters.to_dict().values())


@pytest.mark.parametrize(
    "values",
    [
        {"min_price_euros": 200, "max_price_euros": 100},
        {"min_area_sqm": 0},
        {"max_price_per_sqm": float("nan")},
        {"min_rooms": -1},
        {"min_floor": 3, "max_floor": 2},
        {"elevator": "yes"},
        {"property_types": {"apartment"}},
    ],
)
def test_invalid_filter_constraints_are_rejected(values):
    with pytest.raises((TypeError, ValueError)):
        SearchFilters(**values)


def test_search_requires_name_and_typed_transaction():
    with pytest.raises(ValueError, match="name"):
        NormalizedSearch(name=" ", transaction_type=TransactionType.RENT)

    with pytest.raises(TypeError, match="TransactionType"):
        NormalizedSearch(name="Madrid", transaction_type="rent")
