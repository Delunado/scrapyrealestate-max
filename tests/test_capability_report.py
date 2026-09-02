import pytest

from scrapyrealestate.domain.capabilities import (
    CapabilityReport,
    FilterCapabilities,
    SearchFilterKey,
    active_filter_keys,
    report_capabilities,
)
from scrapyrealestate.domain.search import SearchFilters
from scrapyrealestate.domain.values import PropertyType


def test_active_filter_keys_ignores_unset_fields():
    assert active_filter_keys(SearchFilters()) == frozenset()


def test_active_filter_keys_includes_every_set_field_including_false_booleans():
    filters = SearchFilters(
        min_price_euros=100_000,
        location="Madrid",
        elevator=False,
        property_types=frozenset({PropertyType.APARTMENT}),
    )

    assert active_filter_keys(filters) == frozenset(
        {
            SearchFilterKey.MIN_PRICE_EUROS,
            SearchFilterKey.LOCATION,
            SearchFilterKey.ELEVATOR,
            SearchFilterKey.PROPERTY_TYPES,
        }
    )


def test_active_filter_keys_covers_every_search_filter_key():
    # One filters instance that constrains every field; every SearchFilterKey
    # must show up so a future field addition cannot silently go unreported.
    filters = SearchFilters(
        min_price_euros=1,
        max_price_euros=2,
        min_area_sqm=1,
        max_area_sqm=2,
        min_rooms=1,
        max_rooms=2,
        min_bathrooms=1,
        max_bathrooms=2,
        location="Madrid",
        neighbourhood="Sol",
        min_floor=0,
        max_floor=5,
        elevator=True,
        terrace=False,
        garage=True,
        property_types=frozenset({PropertyType.HOUSE}),
        max_price_per_sqm=1.0,
    )

    assert active_filter_keys(filters) == frozenset(SearchFilterKey)


def test_report_capabilities_only_classifies_requested_filters():
    capabilities = FilterCapabilities(
        remote=frozenset({SearchFilterKey.LOCATION}),
        unsupported=frozenset({SearchFilterKey.GARAGE}),
        local=frozenset(SearchFilterKey)
        - {SearchFilterKey.LOCATION, SearchFilterKey.GARAGE},
    )
    filters = SearchFilters(min_price_euros=100_000, location="Madrid", garage=True)

    report = report_capabilities(filters, capabilities)

    assert report == CapabilityReport(
        remote=frozenset({SearchFilterKey.LOCATION}),
        local=frozenset({SearchFilterKey.MIN_PRICE_EUROS}),
        unsupported=frozenset({SearchFilterKey.GARAGE}),
    )
    assert report.has_unsupported is True


def test_report_capabilities_with_no_active_filters_is_empty_and_not_unsupported():
    capabilities = FilterCapabilities(unsupported=frozenset(SearchFilterKey))

    report = report_capabilities(SearchFilters(), capabilities)

    assert report == CapabilityReport()
    assert report.has_unsupported is False


def test_report_capabilities_rejects_non_filter_capabilities():
    with pytest.raises(TypeError, match="FilterCapabilities"):
        report_capabilities(SearchFilters(), {"remote": [], "local": [], "unsupported": []})


def test_capability_report_to_dict_is_sorted_and_json_ready():
    report = CapabilityReport(
        remote=frozenset({SearchFilterKey.MAX_PRICE_EUROS, SearchFilterKey.LOCATION}),
        local=frozenset({SearchFilterKey.GARAGE}),
    )

    assert report.to_dict() == {
        "remote": ["location", "max_price_euros"],
        "local": ["garage"],
        "unsupported": [],
    }
