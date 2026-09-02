from datetime import datetime, timezone

import pytest
import requests

from scrapyrealestate.domain.notification import NotificationEvent, NotificationEventType
from scrapyrealestate.domain.values import PortalKey
from scrapyrealestate.notifiers import (
    WEBHOOK_SCHEMA_VERSION,
    NotifierConfigurationError,
    WebhookConfig,
    WebhookNotifier,
    build_webhook_payload,
    format_notification,
)


def _event() -> NotificationEvent:
    return NotificationEvent(
        id=10,
        search_id=20,
        search_name="Valencia",
        event_type=NotificationEventType.REAPPEARANCE,
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        listing_id=30,
        listing_title="Casa con terraza",
        portal=PortalKey.HABITACLIA,
        canonical_url="https://example.com/listing/30",
        price_euros=250_000,
        area_sqm=120,
        rooms=4,
        location="Valencia",
        neighbourhood="Campanar",
    )


class StubResponse:
    def __init__(self, status_code=204, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class StubHttpClient:
    def __init__(self, response=None, error=None):
        self.response = response or StubResponse()
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_webhook_posts_versioned_json_with_optional_authorization():
    client = StubHttpClient(StubResponse(headers={"X-Request-ID": "request-1"}))
    notifier = WebhookNotifier(
        WebhookConfig(
            endpoint_url="HTTPS://HOOK.EXAMPLE.COM/events?source=realestate",
            authorization=" Bearer user-secret ",
            timeout_seconds=3,
        ),
        client=client,
    )

    result = notifier.send(_event())

    assert result.success is True
    assert result.provider_message_id == "request-1"
    assert client.calls == [
        (
            "https://hook.example.com/events?source=realestate",
            {
                "json": build_webhook_payload(_event()),
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer user-secret",
                },
                "timeout": 3.0,
            },
        )
    ]


def test_webhook_payload_has_stable_versioned_shape_and_safe_message():
    event = _event()

    payload = build_webhook_payload(event)

    assert payload == {
        "schema_version": WEBHOOK_SCHEMA_VERSION,
        "event": {
            "id": 10,
            "type": "reappearance",
            "occurred_at": "2026-09-02T10:00:00Z",
        },
        "search": {"id": 20, "name": "Valencia"},
        "listing": {
            "id": 30,
            "title": "Casa con terraza",
            "portal": "habitaclia",
            "url": "https://example.com/listing/30",
            "price_euros": 250_000,
            "previous_price_euros": None,
            "area_sqm": 120,
            "rooms": 4,
            "location": "Valencia",
            "neighbourhood": "Campanar",
        },
        "message": {
            "subject": format_notification(event).subject,
            "text": format_notification(event).text,
        },
    }


def test_webhook_omits_authorization_header_when_not_configured():
    client = StubHttpClient()
    notifier = WebhookNotifier(
        WebhookConfig(endpoint_url="https://hook.example.com/events"), client=client
    )

    assert notifier.send(_event()).success is True
    assert client.calls[0][1]["headers"] == {"Accept": "application/json"}


@pytest.mark.parametrize(
    ("error", "category", "diagnostic"),
    [
        (requests.Timeout("user-secret"), "timeout", "webhook request timed out"),
        (
            requests.ConnectionError("user-secret"),
            "transport_error",
            "webhook request failed",
        ),
        (
            RuntimeError("user-secret"),
            "provider_error",
            "webhook delivery failed",
        ),
    ],
)
def test_webhook_safely_classifies_request_failures(error, category, diagnostic):
    notifier = WebhookNotifier(
        WebhookConfig(
            endpoint_url="https://hook.example.com/events",
            authorization="Bearer user-secret",
        ),
        client=StubHttpClient(error=error),
    )

    result = notifier.send(_event())

    assert result.error_category == category
    assert result.diagnostic == diagnostic
    assert "user-secret" not in repr(result)


def test_webhook_safely_classifies_non_success_status():
    notifier = WebhookNotifier(
        WebhookConfig(endpoint_url="https://hook.example.com/events"),
        client=StubHttpClient(StubResponse(status_code=429)),
    )

    result = notifier.send(_event())

    assert result.error_category == "provider_error"
    assert result.diagnostic == "webhook returned HTTP 429"


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint_url": "file:///tmp/hook"},
        {"endpoint_url": "https://user:secret@hook.example.com"},
        {"endpoint_url": "https://hook.example.com/events#secret"},
        {"authorization": "Bearer secret\r\nX-Leak: yes"},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
    ],
)
def test_webhook_validates_channel_configuration(changes):
    values = {"endpoint_url": "https://hook.example.com/events"}
    values.update(changes)

    with pytest.raises(NotifierConfigurationError):
        WebhookConfig(**values)


def test_webhook_configuration_representation_never_contains_authorization():
    config = WebhookConfig(
        endpoint_url="https://hook.example.com/events",
        authorization="Bearer user-secret",
    )

    assert "user-secret" not in repr(config)
    assert "user-secret" not in str(config)
