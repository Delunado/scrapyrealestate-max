"""Shared bounds and stable categories for operational status data."""

from __future__ import annotations

from collections.abc import Collection


MAX_DIAGNOSTIC_CHARS = 2_000
MAX_ERROR_CATEGORY_CHARS = 64
MAX_PROVIDER_MESSAGE_ID_CHARS = 256
DELIVERY_ERROR_CATEGORIES = frozenset(
    {
        "configuration_error",
        "event_error",
        "provider_error",
        "timeout",
        "transport_error",
        "unavailable",
    }
)


def bounded_status_text(value: object | None, *, limit: int) -> str | None:
    """Return stripped, control-safe status text within a fixed character bound."""
    if value is None:
        return None
    control_safe = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character
        for character in str(value)
    )
    text = " ".join(control_safe.split())
    return text[:limit] or None


def stable_error_category(
    value: object | None,
    *,
    allowed: Collection[str],
    fallback: str | None = None,
) -> str | None:
    """Validate a machine-readable category, optionally mapping unknown input."""
    category = bounded_status_text(value, limit=MAX_ERROR_CATEGORY_CHARS)
    if category is None:
        return None
    if category in allowed:
        return category
    if fallback is not None:
        return fallback
    raise ValueError(f"unsupported error category: {category!r}")
