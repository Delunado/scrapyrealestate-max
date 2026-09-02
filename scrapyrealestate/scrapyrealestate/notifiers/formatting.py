"""Plain-text formatting shared by all notification providers.

Provider adapters send this text as plain text. Keeping untrusted listing fields
out of HTML/Markdown modes prevents markup injection and makes the same bounded
representation safe for Telegram, ntfy, webhooks, logs, and tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scrapyrealestate.domain.notification import NotificationEvent, NotificationEventType


MAX_SUBJECT_CHARS = 160
MAX_BODY_CHARS = 3500
MAX_FIELD_CHARS = 500

_EVENT_LABELS = {
    NotificationEventType.NEW_LISTING: "Nueva vivienda",
    NotificationEventType.PRICE_DROP: "Bajada de precio",
    NotificationEventType.PRICE_INCREASE: "Subida de precio",
    NotificationEventType.REAPPEARANCE: "Vivienda de nuevo disponible",
}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    subject: str
    text: str


def format_notification(event: NotificationEvent) -> NotificationMessage:
    """Build a bounded, markup-free user message for any supported event."""
    label = _EVENT_LABELS[event.event_type]
    subject = _truncate(f"{label} · {safe_text(event.search_name)}", MAX_SUBJECT_CHARS)
    lines = [label, safe_text(event.listing_title or "Anuncio inmobiliario")]

    place = " · ".join(
        part
        for part in (
            safe_text(event.location) if event.location else None,
            safe_text(event.neighbourhood) if event.neighbourhood else None,
        )
        if part
    )
    if place:
        lines.append(f"Ubicación: {place}")

    if event.event_type in {
        NotificationEventType.PRICE_DROP,
        NotificationEventType.PRICE_INCREASE,
    } and event.previous_price_euros is not None:
        current = (
            _format_euros(event.price_euros)
            if event.price_euros is not None
            else "desconocido"
        )
        lines.append(
            f"Precio: {current} (antes {_format_euros(event.previous_price_euros)})"
        )
    elif event.price_euros is not None:
        lines.append(f"Precio: {_format_euros(event.price_euros)}")

    details: list[str] = []
    if event.area_sqm is not None:
        area = (
            f"{event.area_sqm:g}"
            if isinstance(event.area_sqm, float)
            else str(event.area_sqm)
        )
        details.append(f"{area} m²")
    if event.rooms is not None:
        details.append(f"{event.rooms} hab.")
    if event.portal is not None:
        details.append(event.portal.value)
    if details:
        lines.append(" · ".join(details))
    if event.canonical_url:
        lines.append(event.canonical_url)

    return NotificationMessage(subject, _truncate("\n".join(lines), MAX_BODY_CHARS))


def safe_text(value: str, *, max_chars: int = MAX_FIELD_CHARS) -> str:
    """Collapse controls/whitespace and bound provider-visible text."""
    cleaned = _WHITESPACE.sub(" ", _CONTROL_CHARACTERS.sub(" ", str(value))).strip()
    return _truncate(cleaned, max_chars)


def _format_euros(value: int) -> str:
    return f"{value:,}".replace(",", ".") + " €"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
