import json

import pytest

from scrapyrealestate.domain.capabilities import (
    FilterCapabilities,
    FilterSupport,
    SearchFilterKey,
)


def make_capabilities() -> FilterCapabilities:
    return FilterCapabilities(
        remote=frozenset(
            {
                SearchFilterKey.MIN_PRICE_EUROS,
                SearchFilterKey.MAX_PRICE_EUROS,
                SearchFilterKey.LOCATION,
            }
        ),
        local=frozenset(
            {
                SearchFilterKey.MIN_AREA_SQM,
                SearchFilterKey.MAX_AREA_SQM,
                SearchFilterKey.MIN_ROOMS,
                SearchFilterKey.MAX_ROOMS,
            }
        ),
        unsupported=frozenset(
            set(SearchFilterKey)
            - {
                SearchFilterKey.MIN_PRICE_EUROS,
                SearchFilterKey.MAX_PRICE_EUROS,
                SearchFilterKey.LOCATION,
                SearchFilterKey.MIN_AREA_SQM,
                SearchFilterKey.MAX_AREA_SQM,
                SearchFilterKey.MIN_ROOMS,
                SearchFilterKey.MAX_ROOMS,
            }
        ),
    )


def test_capabilities_classify_every_filter_and_round_trip_json():
    capabilities = make_capabilities()
    serialized = json.loads(json.dumps(capabilities.to_dict()))

    assert capabilities.support_for(SearchFilterKey.LOCATION) is FilterSupport.REMOTE
    assert capabilities.support_for(SearchFilterKey.MIN_ROOMS) is FilterSupport.LOCAL
    assert capabilities.support_for(SearchFilterKey.GARAGE) is FilterSupport.UNSUPPORTED
    assert FilterCapabilities.from_dict(serialized) == capabilities


def test_capabilities_require_complete_non_overlapping_classification():
    all_filters = frozenset(SearchFilterKey)

    with pytest.raises(ValueError, match="missing filter capabilities"):
        FilterCapabilities(remote=frozenset({SearchFilterKey.LOCATION}))

    with pytest.raises(ValueError, match="exactly one classification"):
        FilterCapabilities(remote=all_filters, local=frozenset({SearchFilterKey.LOCATION}))


def test_capabilities_reject_unknown_groups_and_untyped_keys():
    with pytest.raises(ValueError, match="unknown filter capability groups"):
        FilterCapabilities.from_dict(
            {
                "remote": [key.value for key in SearchFilterKey],
                "sometimes": ["location"],
            }
        )

    with pytest.raises(TypeError, match="SearchFilterKey"):
        FilterCapabilities(remote=frozenset(key.value for key in SearchFilterKey))
