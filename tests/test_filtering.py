from scrapyrealestate.domain.filtering import (
    FilterOutcome,
    evaluate_filters,
    evaluate_listing,
)
from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import (
    PortalKey,
    PropertyType,
    TransactionType,
    TriState,
)


def make_listing(**overrides) -> NormalizedListing:
    values = {
        "portal": PortalKey.PISOSCOM,
        "transaction_type": TransactionType.BUY,
        "title": "Piso en el centro",
        "external_id": "1",
        "property_type": PropertyType.APARTMENT,
        "price_euros": 300_000,
        "area_sqm": 100,
        "rooms": 3,
        "bathrooms": 2,
        "floor": 2,
        "elevator": TriState.YES,
        "terrace": TriState.NO,
        "garage": TriState.YES,
        "location": "Málaga",
        "neighbourhood": "Centro Histórico",
    }
    values.update(overrides)
    return NormalizedListing(**values)


def test_all_supported_local_filters_match_and_are_reported():
    filters = SearchFilters(
        min_price_euros=250_000,
        max_price_euros=350_000,
        min_area_sqm=90,
        max_area_sqm=110,
        min_rooms=2,
        max_rooms=4,
        min_bathrooms=1,
        max_bathrooms=2,
        location="malaga",
        neighbourhood="centro historico",
        min_floor=1,
        max_floor=3,
        elevator=True,
        terrace=False,
        garage=True,
        property_types=frozenset({PropertyType.APARTMENT}),
        max_price_per_sqm=3_100,
    )

    result = evaluate_filters(make_listing(), filters)

    assert result.outcome is FilterOutcome.MATCH
    assert set(result.checks) == {
        "min_price_euros",
        "max_price_euros",
        "min_area_sqm",
        "max_area_sqm",
        "min_rooms",
        "max_rooms",
        "min_bathrooms",
        "max_bathrooms",
        "location",
        "neighbourhood",
        "min_floor",
        "max_floor",
        "elevator",
        "terrace",
        "garage",
        "property_type",
        "max_price_per_sqm",
    }
    assert result.failed_filters == ()
    assert result.unknown_filters == ()


def test_missing_listing_value_produces_unknown_not_false():
    result = evaluate_filters(
        make_listing(rooms=None, elevator=TriState.UNKNOWN),
        SearchFilters(min_rooms=2, elevator=True),
    )

    assert result.outcome is FilterOutcome.UNKNOWN
    assert result.unknown_filters == ("min_rooms", "elevator")
    assert result.to_dict()["outcome"] == "unknown"


def test_definitive_failure_takes_precedence_over_unknown():
    result = evaluate_filters(
        make_listing(rooms=None, price_euros=500_000),
        SearchFilters(min_rooms=2, max_price_euros=350_000),
    )

    assert result.outcome is FilterOutcome.NO_MATCH
    assert result.failed_filters == ("max_price_euros",)
    assert result.unknown_filters == ("min_rooms",)


def test_price_per_square_metre_is_unknown_if_a_component_is_missing():
    result = evaluate_filters(
        make_listing(area_sqm=None),
        SearchFilters(max_price_per_sqm=4_000),
    )

    assert result.checks["max_price_per_sqm"] is FilterOutcome.UNKNOWN


def test_unknown_property_type_is_not_treated_as_other():
    result = evaluate_filters(
        make_listing(property_type=PropertyType.UNKNOWN),
        SearchFilters(property_types=frozenset({PropertyType.HOUSE})),
    )

    assert result.outcome is FilterOutcome.UNKNOWN


def test_search_evaluation_includes_transaction_type():
    search = NormalizedSearch(name="Alquiler", transaction_type=TransactionType.RENT)

    result = evaluate_listing(make_listing(), search)

    assert result.outcome is FilterOutcome.NO_MATCH
    assert result.checks["transaction_type"] is FilterOutcome.NO_MATCH


def test_no_active_filters_matches_every_normalized_listing():
    assert evaluate_filters(make_listing(), SearchFilters()).outcome is FilterOutcome.MATCH
