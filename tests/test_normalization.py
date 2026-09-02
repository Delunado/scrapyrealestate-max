import pytest

from scrapyrealestate.domain.normalization import (
    normalize_area_sqm,
    normalize_count,
    normalize_euro_price,
    normalize_floor,
    normalize_nullable_boolean,
)
from scrapyrealestate.domain.values import TriState


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("325.000 €", 325_000),
        ("1.250,50 €/mes", 1251),
        ("1,250.50 EUR", 1251),
        ("950 €", 950),
        (1250, 1250),
        ("consultar", None),
        ("-1 €", None),
    ],
)
def test_normalize_euro_price(raw, expected):
    assert normalize_euro_price(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("92 m²", 92.0), ("1.250 m2", 1250.0), ("92,5 m²", 92.5), ("0 m²", None)],
)
def test_normalize_area(raw, expected):
    assert normalize_area_sqm(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3 hab.", 3), ("2 baños", 2), (0, 0), ("3,5 habitaciones", None), ("", None)],
)
def test_normalize_room_or_bath_count(raw, expected):
    assert normalize_count(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2ª planta", 2),
        ("Planta 10", 10),
        ("Bajo", 0),
        ("Entreplanta", 0),
        ("Sótano 2", -2),
        ("Ático", None),
    ],
)
def test_normalize_floor(raw, expected):
    assert normalize_floor(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, TriState.YES),
        ("Sí", TriState.YES),
        ("con ascensor", TriState.YES),
        (False, TriState.NO),
        ("sin garaje", TriState.NO),
        (None, TriState.UNKNOWN),
        ("no consta", TriState.UNKNOWN),
    ],
)
def test_normalize_nullable_boolean(raw, expected):
    assert normalize_nullable_boolean(raw) is expected
