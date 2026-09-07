"""Conservative, portal-independent helpers for duplicate candidate matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_TOKEN_ALIASES = {
    "av": "avenida",
    "avda": "avenida",
    "c": "calle",
    "cl": "calle",
    "ctra": "carretera",
    "pº": "paseo",
    "pza": "plaza",
    "plz": "plaza",
    "sta": "santa",
    "sto": "santo",
}
_TITLE_STOP_WORDS = frozenset(
    {
        "a",
        "al",
        "con",
        "de",
        "del",
        "el",
        "en",
        "la",
        "las",
        "los",
        "para",
        "por",
        "se",
        "un",
        "una",
        "y",
    }
)


def normalize_spanish_tokens(value: str | None) -> tuple[str, ...]:
    """Return stable lowercase word/number tokens with accents removed.

    The output intentionally keeps content words and numbers.  It only expands a
    short list of unambiguous address abbreviations; fuzzy spelling correction
    would make cross-site duplicate suggestions too permissive.
    """
    if value is None:
        return ()
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.strip().casefold())
        if not unicodedata.combining(character)
    )
    tokens = _TOKEN_PATTERN.findall(folded)
    return tuple(_TOKEN_ALIASES.get(token, token) for token in tokens)


def normalize_location_tokens(*values: str | None) -> frozenset[str]:
    """Normalize municipality/neighbourhood fields for exact-token narrowing."""
    return frozenset(_tokens_from(values))


def normalize_address_tokens(
    street: str | None,
    street_number: str | None = None,
) -> frozenset[str]:
    """Normalize a street and number without inventing missing address detail."""
    return frozenset(_tokens_from((street, street_number)))


def normalize_title_tokens(title: str | None) -> frozenset[str]:
    """Normalize title content, dropping only low-information Spanish glue words."""
    return frozenset(
        token
        for token in normalize_spanish_tokens(title)
        if token not in _TITLE_STOP_WORDS
    )


def title_similarity(first: str | None, second: str | None) -> float | None:
    """Return Sørensen-Dice token similarity, or ``None`` for missing content."""
    first_tokens = normalize_title_tokens(first)
    second_tokens = normalize_title_tokens(second)
    if not first_tokens or not second_tokens:
        return None
    return 2 * len(first_tokens & second_tokens) / (
        len(first_tokens) + len(second_tokens)
    )


def prices_similar(
    first: int | None,
    second: int | None,
    *,
    max_relative_difference: float = 0.03,
) -> bool | None:
    """Compare known prices within a strict relative tolerance."""
    return _relative_comparison(first, second, max_relative_difference)


def areas_similar(
    first: float | None,
    second: float | None,
    *,
    max_relative_difference: float = 0.05,
    max_absolute_difference: float = 3.0,
) -> bool | None:
    """Compare known areas, allowing small portal rounding differences."""
    if first is None or second is None:
        return None
    if first <= 0 or second <= 0:
        return False
    difference = abs(first - second)
    return difference <= max_absolute_difference or (
        difference / max(first, second) <= max_relative_difference
    )


def bedrooms_similar(first: int | None, second: int | None) -> bool | None:
    """Require exact equality for known bedroom counts."""
    if first is None or second is None:
        return None
    if first < 0 or second < 0:
        return False
    return first == second


def relative_difference(first: float | int, second: float | int) -> float:
    """Return a symmetric relative difference suitable for evidence display."""
    denominator = max(abs(float(first)), abs(float(second)))
    if denominator == 0:
        return 0.0
    return abs(float(first) - float(second)) / denominator


def _relative_comparison(
    first: float | int | None,
    second: float | int | None,
    tolerance: float,
) -> bool | None:
    if tolerance < 0:
        raise ValueError("tolerance cannot be negative")
    if first is None or second is None:
        return None
    if first < 0 or second < 0:
        return False
    return relative_difference(first, second) <= tolerance


def _tokens_from(values: Iterable[str | None]) -> Iterable[str]:
    for value in values:
        yield from normalize_spanish_tokens(value)
