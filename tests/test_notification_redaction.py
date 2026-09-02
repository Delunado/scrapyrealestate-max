import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask, render_template_string

from scrapyrealestate.domain.notification import NotificationEvent, NotificationEventType
from scrapyrealestate.notifiers import (
    DeliveryResult,
    NtfyConfig,
    NotifierConfigurationError,
    NotifierRegistry,
    TelegramConfig,
    WebhookConfig,
)
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import (
    NotificationProvider,
    NotificationRepository,
)
from scrapyrealestate.security import (
    REDACTED,
    SecretRedactingFormatter,
    configured_notification_secrets,
)
from scrapyrealestate.services.notification_delivery import DurableNotificationDispatcher
from scrapyrealestate.services.notification_routing import NotificationRouter


SECRETS = {
    NotificationProvider.TELEGRAM: "telegram-secret",
    NotificationProvider.NTFY: "ntfy-secret",
    NotificationProvider.WEBHOOK: "webhook-secret",
}


class SecretEchoingNotifier:
    def __init__(self, secret):
        self.secret = secret

    def send(self, event):
        return DeliveryResult(
            False,
            provider_message_id=f"id:{self.secret}",
            error_category=f"error:{self.secret}",
            diagnostic=f"diagnostic:{self.secret}",
        )


@pytest.fixture
def setup(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
        ).fetchone()[0]
        repository = NotificationRepository(connection)
        channels = []
        for provider, secret in SECRETS.items():
            channel = repository.create_channel(
                provider.value,
                provider,
                config=_public_config(provider),
                secret_config={"credential": secret, "nested": [secret]},
            )
            repository.assign_channel(search_id, channel.id)
            channels.append(channel)
        yield repository, search_id, tuple(channels)


def _public_config(provider):
    if provider is NotificationProvider.TELEGRAM:
        return {"chat_id": "123"}
    if provider is NotificationProvider.NTFY:
        return {"server_url": "https://ntfy.example.com", "topic": "homes"}
    return {"endpoint_url": "https://hook.example.com/events"}


def _event(search_id):
    return NotificationEvent(
        id=1,
        search_id=search_id,
        search_name="A",
        event_type=NotificationEventType.NEW_LISTING,
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        listing_title="Piso",
    )


def _registry():
    registry = NotifierRegistry()
    for provider in NotificationProvider:
        registry.register(
            provider,
            lambda channel: SecretEchoingNotifier(channel.secret_config["credential"]),
        )
    return registry


def test_normal_reads_and_template_context_mask_every_provider_secret(setup):
    repository, _, _ = setup
    channels = repository.list_channels()

    app = Flask(__name__)
    with app.app_context():
        rendered = render_template_string("{{ channels }}", channels=channels)

    combined = repr(channels) + rendered
    assert "********" in combined
    assert all(secret not in combined for secret in SECRETS.values())


def test_router_redacts_provider_controlled_status_fields(setup):
    repository, search_id, _ = setup

    result = NotificationRouter(repository, _registry()).route(_event(search_id))

    rendered_status = repr(result)
    assert REDACTED in rendered_status
    assert all(secret not in rendered_status for secret in SECRETS.values())


def test_durable_status_rows_redact_provider_controlled_fields(setup):
    repository, search_id, _ = setup
    event = repository.create_event(
        search_id,
        NotificationEventType.NEW_LISTING,
        "redaction-event",
        datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
    ).event
    now = datetime(2026, 9, 2, 10, 1, tzinfo=timezone.utc)
    dispatcher = DurableNotificationDispatcher(
        repository, _registry(), clock=lambda: now
    )

    assert dispatcher.enqueue(event.id) == 3
    outcomes = dispatcher.dispatch_available()

    rows = repository.connection.execute(
            """
            SELECT error_category, redacted_diagnostic, provider_message_id
            FROM notification_delivery_attempts WHERE status = 'failed'
            """
        ).fetchall()
    status_data = repr(outcomes) + repr([tuple(row) for row in rows])
    assert REDACTED in status_data
    assert all(secret not in status_data for secret in SECRETS.values())


def test_provider_configuration_exceptions_never_include_secrets():
    cases = (
        lambda: TelegramConfig(chat_id="", bot_token="telegram-secret"),
        lambda: NtfyConfig(
            server_url="not-a-url", topic="homes", access_token="ntfy-secret"
        ),
        lambda: WebhookConfig(
            endpoint_url="https://hook.example.com",
            authorization="Bearer webhook-secret\r\nInjected: value",
        ),
    )

    messages = []
    for create in cases:
        with pytest.raises(NotifierConfigurationError) as raised:
            create()
        messages.append(str(raised.value))

    combined = " ".join(messages)
    assert all(secret not in combined for secret in SECRETS.values())


def test_all_provider_secrets_are_flattened_and_redacted_from_logs(setup):
    repository, search_id, _ = setup
    delivery_channels = repository.delivery_channels_for_search(search_id)
    secrets = configured_notification_secrets(
        *(channel.secret_config for channel in delivery_channels)
    )
    stream = _ListStream()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SecretRedactingFormatter("%(message)s", secrets=secrets))
    logger = logging.getLogger("notification-redaction-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("credentials: %s", " ".join(SECRETS.values()))

    rendered = "".join(stream.values)
    assert REDACTED in rendered
    assert all(secret not in rendered for secret in SECRETS.values())


class _ListStream:
    def __init__(self):
        self.values = []

    def write(self, value):
        self.values.append(value)

    def flush(self):
        pass
