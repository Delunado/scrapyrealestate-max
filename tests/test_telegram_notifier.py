from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scrapyrealestate.domain.notification import NotificationEvent, NotificationEventType
from scrapyrealestate.notifiers import (
    NotifierConfigurationError,
    TelegramConfig,
    TelegramNotifier,
    format_notification,
)


def _event() -> NotificationEvent:
    return NotificationEvent(
        id=1,
        search_id=2,
        search_name="Centro",
        event_type=NotificationEventType.NEW_LISTING,
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        listing_title="Piso <b>sin markup</b>",
        canonical_url="https://example.com/listing/1",
        price_euros=180_000,
    )


class StubTelegramClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def send_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_telegram_uses_shared_plain_text_formatter_and_returns_message_id():
    client = StubTelegramClient(SimpleNamespace(message_id=456))
    notifier = TelegramNotifier(
        TelegramConfig(chat_id=" -100123 ", bot_token=" user-token "),
        client=client,
    )

    result = notifier.send(_event())

    assert result.success is True
    assert result.provider_message_id == "456"
    assert client.calls == [
        (("-100123", format_notification(_event()).text), {}),
    ]
    assert "<b>sin markup</b>" in client.calls[0][0][1]


@pytest.mark.parametrize(
    ("error", "category", "diagnostic"),
    [
        (TimeoutError("user-token"), "timeout", "Telegram request timed out"),
        (RuntimeError("user-token"), "provider_error", "Telegram delivery failed"),
    ],
)
def test_telegram_classifies_failures_without_exposing_provider_errors(
    error, category, diagnostic
):
    notifier = TelegramNotifier(
        TelegramConfig(chat_id="123", bot_token="user-token"),
        client=StubTelegramClient(error=error),
    )

    result = notifier.send(_event())

    assert result.success is False
    assert result.error_category == category
    assert result.diagnostic == diagnostic
    assert "user-token" not in repr(result)


@pytest.mark.parametrize(
    ("chat_id", "bot_token", "field_name"),
    [("", "token", "chat_id"), ("123", "", "bot_token")],
)
def test_telegram_requires_complete_user_configuration(
    chat_id, bot_token, field_name
):
    with pytest.raises(NotifierConfigurationError, match=field_name):
        TelegramConfig(chat_id=chat_id, bot_token=bot_token)


def test_telegram_configuration_representation_never_contains_token():
    config = TelegramConfig(chat_id="123", bot_token="user-token")

    assert "user-token" not in repr(config)
    assert "user-token" not in str(config)
