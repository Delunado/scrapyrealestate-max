from datetime import datetime, timezone

import pytest

from scrapyrealestate.domain.notification import NotificationEvent, NotificationEventType
from scrapyrealestate.domain.values import PortalKey
from scrapyrealestate.notifiers import DeliveryResult, format_notification
from scrapyrealestate.notifiers.formatting import (
    MAX_BODY_CHARS,
    MAX_SUBJECT_CHARS,
    safe_text,
)


def _event(event_type: NotificationEventType, **changes) -> NotificationEvent:
    values = {
        "id": 1,
        "search_id": 2,
        "search_name": "Madrid centro",
        "event_type": event_type,
        "occurred_at": datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        "listing_id": 3,
        "listing_title": "Piso luminoso",
        "portal": PortalKey.PISOSCOM,
        "canonical_url": "https://www.pisos.com/comprar/piso-3/",
        "price_euros": 190_000,
        "area_sqm": 85.5,
        "rooms": 3,
        "location": "Madrid",
        "neighbourhood": "Centro",
    }
    values.update(changes)
    return NotificationEvent(**values)


@pytest.mark.parametrize(
    ("event_type", "label"),
    [
        (NotificationEventType.NEW_LISTING, "Nueva vivienda"),
        (NotificationEventType.PRICE_DROP, "Bajada de precio"),
        (NotificationEventType.PRICE_INCREASE, "Subida de precio"),
        (NotificationEventType.REAPPEARANCE, "Vivienda de nuevo disponible"),
    ],
)
def test_shared_formatter_covers_every_event_type(event_type, label):
    event = _event(event_type, previous_price_euros=200_000)

    message = format_notification(event)

    assert message.subject == f"{label} · Madrid centro"
    assert message.text.startswith(f"{label}\nPiso luminoso\n")
    assert "Ubicación: Madrid · Centro" in message.text
    assert "190.000 €" in message.text
    assert "85.5 m² · 3 hab. · pisoscom" in message.text
    assert message.text.endswith("https://www.pisos.com/comprar/piso-3/")


def test_price_change_format_includes_previous_price():
    message = format_notification(
        _event(NotificationEventType.PRICE_DROP, previous_price_euros=210_000)
    )

    assert "Precio: 190.000 € (antes 210.000 €)" in message.text


def test_formatting_is_plain_text_control_safe_and_bounded():
    event = _event(
        NotificationEventType.NEW_LISTING,
        search_name="Busca\x00\nalerta " + "x" * 300,
        listing_title="<b>No interpretar</b>\r\n" + "y" * 4000,
    )

    message = format_notification(event)

    assert "\x00" not in message.subject + message.text
    assert "Busca alerta" in message.subject
    assert "<b>No interpretar</b>" in message.text
    assert len(message.subject) <= MAX_SUBJECT_CHARS
    assert len(message.text) <= MAX_BODY_CHARS
    assert safe_text(" uno\t\ndos ") == "uno dos"


def test_event_validates_provider_safe_data():
    with pytest.raises(ValueError, match="timezone-aware"):
        _event(NotificationEventType.NEW_LISTING, occurred_at=datetime(2026, 9, 2))
    with pytest.raises(ValueError, match="absolute HTTP"):
        _event(NotificationEventType.NEW_LISTING, canonical_url="javascript:alert(1)")


def test_delivery_result_enforces_classified_failures():
    assert DeliveryResult.delivered("provider-1").success is True
    failure = DeliveryResult.failed("timeout", "request timed out")
    assert failure.success is False
    assert failure.error_category == "timeout"
    with pytest.raises(ValueError, match="requires an error category"):
        DeliveryResult(False)
