import pytest

from scrapyrealestate.notifiers import (
    NtfyNotifier,
    NotifierConfigurationError,
    TelegramNotifier,
    WebhookNotifier,
    build_default_notifier_registry,
)
from scrapyrealestate.persistence.notifications import (
    NotificationChannelDeliveryConfig,
    NotificationProvider,
)


@pytest.mark.parametrize(
    ("provider", "config", "secrets", "notifier_type"),
    [
        (
            NotificationProvider.TELEGRAM,
            {"chat_id": "123"},
            {"bot_token": "token"},
            TelegramNotifier,
        ),
        (
            NotificationProvider.NTFY,
            {"server_url": "https://ntfy.example.com", "topic": "homes"},
            {"access_token": "token"},
            NtfyNotifier,
        ),
        (
            NotificationProvider.WEBHOOK,
            {"endpoint_url": "https://hook.example.com/events"},
            {"authorization": "Bearer token"},
            WebhookNotifier,
        ),
    ],
)
def test_default_registry_builds_every_provider(
    provider, config, secrets, notifier_type
):
    channel = NotificationChannelDeliveryConfig(
        id=1,
        name="Channel",
        provider=provider,
        config=config,
        secret_config=secrets,
    )

    notifier = build_default_notifier_registry().create(channel)

    assert isinstance(notifier, notifier_type)


def test_default_registry_rejects_incomplete_provider_configuration():
    channel = NotificationChannelDeliveryConfig(
        id=1,
        name="Telegram",
        provider=NotificationProvider.TELEGRAM,
        config={"chat_id": "123"},
        secret_config={},
    )

    with pytest.raises(NotifierConfigurationError, match="bot_token"):
        build_default_notifier_registry().create(channel)
