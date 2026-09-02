"""Locale-aware parsing of display values emitted by Spanish portals."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from scrapyrealestate.domain.values import TriState


_NUMBER_PATTERN = re.compile(r"[-+]?\d[\d.,\s\u00a0]*")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _decimal_number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None

    match = _NUMBER_PATTERN.search(str(value))
    if match is None:
        return None
    token = re.sub(r"[\s\u00a0]", "", match.group())
    sign = ""
    if token[:1] in {"-", "+"}:
        sign, token = token[0], token[1:]

    decimal_separator: str | None = None
    if "." in token and "," in token:
        last_separator = "." if token.rfind(".") > token.rfind(",") else ","
        if len(token.rsplit(last_separator, 1)[1]) in {1, 2}:
            decimal_separator = last_separator
    else:
        separator = "," if "," in token else "." if "." in token else None
        if separator and token.count(separator) == 1:
            digits_after = len(token.rsplit(separator, 1)[1])
            if digits_after in {1, 2}:
                decimal_separator = separator

    if decimal_separator is None:
        normalized = token.replace(".", "").replace(",", "")
    else:
        thousands_separator = "," if decimal_separator == "." else "."
        normalized = token.replace(thousands_separator, "")
        normalized = normalized.replace(decimal_separator, ".")
    try:
        number = Decimal(sign + normalized)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def normalize_euro_price(value: Any) -> int | None:
    """Parse a displayed euro amount into whole euros using half-up rounding."""
    number = _decimal_number(value)
    if number is None or number < 0:
        return None
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_area_sqm(value: Any) -> float | None:
    """Parse square metres, returning ``None`` for missing or non-positive area."""
    number = _decimal_number(value)
    if number is None or number <= 0:
        return None
    return float(number)


def normalize_count(value: Any) -> int | None:
    """Parse a non-negative whole room or bathroom count."""
    number = _decimal_number(value)
    if number is None or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def normalize_floor(value: Any) -> int | None:
    """Parse common Spanish floor labels into a signed integer level."""
    if value is None or isinstance(value, bool):
        return None
    text = _fold(str(value).strip())
    if not text:
        return None
    if "sotano" in text:
        match = re.search(r"\d+", text)
        return -int(match.group()) if match else -1
    if any(label in text for label in ("bajo", "entreplanta", "principal")):
        return 0
    number = _decimal_number(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def normalize_nullable_boolean(value: Any) -> TriState:
    """Normalize portal truth values without treating missing data as false."""
    if isinstance(value, TriState):
        return value
    if value is True or value == 1:
        return TriState.YES
    if value is False or value == 0:
        return TriState.NO
    if value is None:
        return TriState.UNKNOWN

    text = _fold(str(value).strip())
    if not text or text in {"-", "n/a", "n/d", "unknown", "desconocido"}:
        return TriState.UNKNOWN
    if text in {"si", "yes", "true", "con", "incluido", "disponible"} or text.startswith(
        "con "
    ):
        return TriState.YES
    if text in {"no", "false", "sin", "no disponible"} or text.startswith("sin "):
        return TriState.NO
    return TriState.UNKNOWN
