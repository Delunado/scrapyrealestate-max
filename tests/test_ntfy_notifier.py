from datetime import datetime, timezone

import pytest
import requests

from scrapyrealestate.domain.notification import NotificationEvent, NotificationEventType
from scrapyrealestate.notifiers import (
    NtfyConfig,
    NtfyNotifier,
    NotifierConfigurationError,
    format_notification,
)


def _event() -> NotificationEvent:
    return NotificationEvent(
        id=1,
        search_id=2,
        search_name="Málaga centro",
        event_type=NotificationEventType.PRICE_DROP,
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        listing_title="Ático luminoso",
        canonical_url="https://example.com/listing/1",
        price_euros=180_000,
        previous_price_euros=190_000,
    )


class StubResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


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


def test_ntfy_publishes_shared_message_as_utf8_safe_json_with_auth():
    client = StubHttpClient(StubResponse(payload={"id": "ntfy-message-1"}))
    notifier = NtfyNotifier(
        NtfyConfig(
            server_url="HTTPS://NTFY.EXAMPLE.COM/",
            topic="homes_alerts",
            access_token=" access-token ",
            timeout_seconds=4,
        ),
        client=client,
    )

    result = notifier.send(_event())

    message = format_notification(_event())
    assert result.success is True
    assert result.provider_message_id == "ntfy-message-1"
    assert client.calls == [
        (
            "https://ntfy.example.com/",
            {
                "json": {
                    "topic": "homes_alerts",
                    "title": message.subject,
                    "message": message.text,
                },
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer access-token",
                },
                "timeout": 4.0,
            },
        )
    ]


def test_ntfy_can_publish_without_authentication():
    client = StubHttpClient()
    notifier = NtfyNotifier(
        NtfyConfig(server_url="https://ntfy.sh", topic="public-topic"), client=client
    )

    assert notifier.send(_event()).success is True
    assert client.calls[0][1]["headers"] == {"Accept": "application/json"}


@pytest.mark.parametrize(
    ("error", "category", "diagnostic"),
    [
        (requests.Timeout("access-token"), "timeout", "ntfy request timed out"),
        (
            requests.ConnectionError("access-token"),
            "transport_error",
            "ntfy request failed",
        ),
        (RuntimeError("access-token"), "provider_error", "ntfy delivery failed"),
    ],
)
def test_ntfy_safely_classifies_http_errors(error, category, diagnostic):
    notifier = NtfyNotifier(
        NtfyConfig(
            server_url="https://ntfy.example.com",
            topic="homes",
            access_token="access-token",
        ),
        client=StubHttpClient(error=error),
    )

    result = notifier.send(_event())

    assert result.error_category == category
    assert result.diagnostic == diagnostic
    assert "access-token" not in repr(result)


def test_ntfy_rejects_non_success_responses_without_copying_response_body():
    client = StubHttpClient(StubResponse(403, {"error": "access-token"}))
    notifier = NtfyNotifier(
        NtfyConfig(server_url="https://ntfy.example.com", topic="homes"),
        client=client,
    )

    result = notifier.send(_event())

    assert result.error_category == "provider_error"
    assert result.diagnostic == "ntfy returned HTTP 403"
    assert "access-token" not in repr(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"server_url": "ftp://ntfy.example.com"},
        {"server_url": "https://token@ntfy.example.com"},
        {"server_url": "https://ntfy.example.com?token=secret"},
        {"topic": "bad/topic"},
        {"topic": ""},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
    ],
)
def test_ntfy_validates_channel_configuration(changes):
    values = {"server_url": "https://ntfy.example.com", "topic": "homes"}
    values.update(changes)

    with pytest.raises(NotifierConfigurationError):
        NtfyConfig(**values)


def test_ntfy_configuration_representation_never_contains_access_token():
    config = NtfyConfig(
        server_url="https://ntfy.example.com",
        topic="homes",
        access_token="access-token",
    )

    assert "access-token" not in repr(config)
    assert "access-token" not in str(config)
