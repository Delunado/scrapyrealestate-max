from scrapyrealestate.domain.values import PortalKey
from scrapyrealestate.persistence.duplicates import DuplicateListingSnapshot
from scrapyrealestate.services.duplicate_candidates import (
    DuplicateCandidatePair,
    DuplicateCandidateScorer,
)


def _snapshot(identifier, portal, **changes):
    values = {
        "id": identifier,
        "portal": portal,
        "transaction_type": "buy",
        "property_type": "apartment",
        "title": "Piso luminoso reformado con terraza",
        "price_euros": 300_000,
        "area_sqm": 100.0,
        "rooms": 3,
        "location": "Madrid",
        "neighbourhood": "Chamberí",
        "street": "Calle de Santa Engracia",
        "street_number": "42",
    }
    values.update(changes)
    return DuplicateListingSnapshot(**values)


def _pair(first_changes=None, second_changes=None):
    return DuplicateCandidatePair(
        _snapshot(1, PortalKey.PISOSCOM, **(first_changes or {})),
        _snapshot(2, PortalKey.HABITACLIA, **(second_changes or {})),
    )


def test_strong_exact_address_pair_is_eligible_with_explainable_score():
    result = DuplicateCandidateScorer().score(
        _pair(
            second_changes={
                "title": "Piso reformado, luminoso y con terraza",
                "price_euros": 305_000,
                "area_sqm": 102.0,
                "street": "C/ Santa Engracia",
            }
        )
    )

    assert result.eligible is True
    assert result.score >= 0.85
    assert result.confidence == "high"
    assert next(reason for reason in result.reasons if reason["code"] == "exact_address")[
        "matched"
    ] is True


def test_same_city_and_generic_title_do_not_create_false_positive():
    result = DuplicateCandidateScorer().score(
        _pair(
            first_changes={"title": "Piso en venta", "street": None, "street_number": None},
            second_changes={
                "title": "Piso en venta",
                "street": None,
                "street_number": None,
                "neighbourhood": None,
            },
        )
    )

    assert result.eligible is False
    assert any(reason["code"] == "identity_gate" for reason in result.reasons)


def test_address_less_pair_requires_distinctive_title_neighbourhood_and_structure():
    result = DuplicateCandidateScorer().score(
        _pair(
            first_changes={"street": None, "street_number": None},
            second_changes={
                "street": None,
                "street_number": None,
                "title": "Piso reformado y luminoso con terraza",
                "price_euros": 303_000,
                "area_sqm": 101.0,
            },
        )
    )

    assert result.eligible is True


def test_different_street_numbers_are_a_hard_conflict_even_with_similar_data():
    result = DuplicateCandidateScorer().score(
        _pair(second_changes={"street_number": "44"})
    )

    assert result.eligible is False
    conflict = next(reason for reason in result.reasons if reason["code"] == "hard_conflict")
    assert "address_conflict" in conflict["details"]


def test_known_structural_conflicts_prevent_false_positive():
    for changes, conflict_code in (
        ({"rooms": 2}, "bedroom_conflict"),
        ({"area_sqm": 120.0}, "area_conflict"),
        ({"price_euros": 340_000}, "price_conflict"),
        ({"property_type": "house"}, "property_type_conflict"),
    ):
        result = DuplicateCandidateScorer().score(_pair(second_changes=changes))
        assert result.eligible is False
        conflict = next(
            reason for reason in result.reasons if reason["code"] == "hard_conflict"
        )
        assert conflict_code in conflict["details"]


def test_unknown_values_never_earn_weight_or_bypass_identity_gate():
    result = DuplicateCandidateScorer().score(
        _pair(
            first_changes={"street": None, "street_number": None},
            second_changes={
                "street": None,
                "street_number": None,
                "rooms": None,
                "area_sqm": None,
                "price_euros": None,
            },
        )
    )

    assert result.eligible is False
    for code in ("bedrooms", "area", "price"):
        reason = next(reason for reason in result.reasons if reason["code"] == code)
        assert reason["contribution"] == 0.0
