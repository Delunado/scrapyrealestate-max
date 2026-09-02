from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.domain.notification import NotificationPreferences
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import (
    MASKED_SECRET,
    NotificationEventType,
    NotificationProvider,
    NotificationRepository,
)


@pytest.fixture
def repository(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
        ).fetchone()[0]
        listing_id = connection.execute(
            """
            INSERT INTO listings (
                portal_key, external_id, transaction_type, title,
                first_seen_at, last_seen_at
            ) VALUES ('pisoscom', '123', 'buy', 'Piso',
                      '2026-09-01T10:00:00Z', '2026-09-01T10:00:00Z')
            RETURNING id
            """
        ).fetchone()[0]
        yield NotificationRepository(connection), connection, search_id, listing_id


def test_channel_reads_mask_secrets_recursively(repository):
    notifications, connection, _, _ = repository
    channel = notifications.create_channel(
        "Telegram",
        NotificationProvider.TELEGRAM,
        config={"chat_id": "123"},
        secret_config={"bot_token": "top-secret", "nested": {"key": "hidden"}},
    )

    assert channel.config == {"chat_id": "123"}
    assert channel.secret_config == {
        "bot_token": MASKED_SECRET,
        "nested": {"key": MASKED_SECRET},
    }
    assert "top-secret" not in repr(channel)
    assert "top-secret" not in repr(notifications.list_channels())
    stored = connection.execute(
        "SELECT secret_config_json FROM notification_channels WHERE id = ?",
        (channel.id,),
    ).fetchone()[0]
    assert "top-secret" in stored


def test_delivery_channel_read_is_explicit_and_repr_safe(repository):
    notifications, _, search_id, _ = repository
    channel = notifications.create_channel(
        "Telegram",
        NotificationProvider.TELEGRAM,
        config={"chat_id": "123"},
        secret_config={"bot_token": "top-secret"},
    )
    notifications.assign_channel(search_id, channel.id)

    (delivery_config,) = notifications.delivery_channels_for_search(search_id)

    assert delivery_config.secret_config == {"bot_token": "top-secret"}
    assert "top-secret" not in repr(delivery_config)


def test_channel_assignment_is_idempotent(repository):
    notifications, _, search_id, _ = repository
    channel = notifications.create_channel("ntfy", NotificationProvider.NTFY)

    assert notifications.assign_channel(search_id, channel.id) is True
    assert notifications.assign_channel(search_id, channel.id) is False
    assert notifications.channels_for_search(search_id) == (channel,)
    assert notifications.unassign_channel(search_id, channel.id) is True


def test_event_creation_is_idempotent(repository):
    notifications, _, search_id, listing_id = repository
    values = {
        "search_id": search_id,
        "listing_id": listing_id,
        "event_type": NotificationEventType.PRICE_DROP,
        "deduplication_key": "price-drop:pisoscom:123:190000",
        "occurred_at": datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        "payload": {"old_price": 200_000, "new_price": 190_000},
    }

    first = notifications.create_event(**values)
    repeated = notifications.create_event(**values)

    assert first.created is True
    assert repeated.created is False
    assert repeated.event == first.event


def test_delivery_attempt_identity_is_retry_safe(repository):
    notifications, _, search_id, listing_id = repository
    channel = notifications.create_channel("Webhook", NotificationProvider.WEBHOOK)
    event = notifications.create_event(
        search_id,
        NotificationEventType.NEW_LISTING,
        "new:pisoscom:123",
        datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        listing_id=listing_id,
    ).event

    first, first_created = notifications.ensure_delivery_attempt(event.id, channel.id)
    repeated, repeated_created = notifications.ensure_delivery_attempt(
        event.id, channel.id
    )
    retry = notifications.create_retry(event.id, channel.id)

    assert first_created is True
    assert repeated_created is False
    assert repeated == first
    assert retry.attempt_number == 2


def test_search_event_preferences_default_to_new_listings_and_price_drops(repository):
    notifications, _, search_id, _ = repository

    preferences = notifications.preferences_for_search(search_id)

    assert preferences == NotificationPreferences()
    assert preferences.to_dict() == {
        "new_listing": True,
        "price_drop": True,
        "price_increase": False,
        "reappearance": False,
    }


def test_search_event_preferences_are_configurable_and_persisted(repository):
    notifications, connection, search_id, listing_id = repository
    configured = NotificationPreferences(
        new_listing=False,
        price_drop=True,
        price_increase=True,
        reappearance=True,
    )

    assert notifications.set_event_preferences(search_id, configured) == configured
    assert notifications.preferences_for_search(search_id) == configured
    row = connection.execute(
        "SELECT * FROM search_notification_preferences WHERE search_id = ?",
        (search_id,),
    ).fetchone()
    assert tuple(
        row[column]
        for column in (
            "notify_new_listing",
            "notify_price_drop",
            "notify_price_increase",
            "notify_reappearance",
        )
    ) == (0, 1, 1, 1)

    events = tuple(
        notifications.create_event(
            search_id,
            event_type,
            f"preference-test:{event_type.value}",
            datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
            listing_id=listing_id,
        ).event
        for event_type in NotificationEventType
    )
    assert [
        event.event_type
        for event in notifications.select_enabled_events(search_id, events)
    ] == [
        NotificationEventType.PRICE_DROP,
        NotificationEventType.PRICE_INCREASE,
        NotificationEventType.REAPPEARANCE,
    ]


def test_preferences_require_an_existing_search_and_matching_events(repository):
    notifications, _, search_id, listing_id = repository
    with pytest.raises(LookupError, match="does not exist"):
        notifications.preferences_for_search(999)

    second_search_id = notifications.connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('B', 'buy') RETURNING id"
    ).fetchone()[0]
    event = notifications.create_event(
        second_search_id,
        NotificationEventType.NEW_LISTING,
        "other-search-event",
        datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        listing_id=listing_id,
    ).event
    with pytest.raises(ValueError, match="originate"):
        notifications.select_enabled_events(search_id, (event,))
