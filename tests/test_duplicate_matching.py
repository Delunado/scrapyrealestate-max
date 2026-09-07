import json

import pytest

from scrapyrealestate.domain.duplicate_matching import (
    areas_similar,
    bedrooms_similar,
    normalize_address_tokens,
    normalize_location_tokens,
    normalize_spanish_tokens,
    prices_similar,
    relative_difference,
    title_similarity,
)


def test_spanish_text_fixture_normalizes_accents_and_address_abbreviations(load_fixture):
    fixture = json.loads(load_fixture("duplicates/spanish_text.json"))

    for case in fixture["token_cases"]:
        assert normalize_spanish_tokens(case["source"]) == tuple(case["tokens"])


def test_location_and_address_tokens_remain_separate_and_conservative():
    assert normalize_location_tokens("Madrid", "Chamberí") == {"madrid", "chamberi"}
    assert normalize_address_tokens("C/ Santa Engracia", "12 B") == {
        "calle",
        "santa",
        "engracia",
        "12",
        "b",
    }
    assert normalize_address_tokens(None, None) == frozenset()


def test_spanish_title_similarity_is_order_independent(load_fixture):
    fixture = json.loads(load_fixture("duplicates/spanish_text.json"))

    for case in fixture["title_pairs"]:
        assert title_similarity(case["first"], case["second"]) == pytest.approx(
            case["similarity"]
        )
    assert title_similarity("de la y", "Piso") is None


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (200_000, 204_000, True),
        (200_000, 210_000, False),
        (None, 200_000, None),
    ],
)
def test_price_comparison_is_strict_and_three_state(first, second, expected):
    assert prices_similar(first, second) is expected


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (80.0, 83.0, True),
        (100.0, 104.0, True),
        (80.0, 85.0, False),
        (None, 80.0, None),
    ],
)
def test_area_comparison_allows_only_small_rounding_differences(first, second, expected):
    assert areas_similar(first, second) is expected


def test_bedroom_comparison_requires_exact_known_values():
    assert bedrooms_similar(3, 3) is True
    assert bedrooms_similar(3, 2) is False
    assert bedrooms_similar(3, None) is None


def test_relative_difference_is_symmetric_and_handles_zero():
    assert relative_difference(100, 95) == pytest.approx(0.05)
    assert relative_difference(95, 100) == pytest.approx(0.05)
    assert relative_difference(0, 0) == 0.0
