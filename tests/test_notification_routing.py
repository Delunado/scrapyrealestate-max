from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapyrealestate.domain.notification import (
    NotificationEvent,
    NotificationEventType,
    NotificationPreferences,
)
from scrapyrealestate.notifiers import DeliveryResult, NotifierRegistry
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import (
    NotificationProvider,
    NotificationRepository,
)
from scrapyrealestate.services.notification_routing import NotificationRouter


class RecordingNotifier:
    def __init__(self, calls, result=None, error=None):
        self.calls = calls
        self.result = result or DeliveryResult.delivered("ok")
        self.error = error

    def send(self, event):
        self.calls.append(event)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def setup(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
        ).fetchone()[0]
        repository = NotificationRepository(connection)
        yield repository, search_id


def _event(search_id, event_type=NotificationEventType.NEW_LISTING):
    return NotificationEvent(
        id=1,
        search_id=search_id,
        search_name="A",
        event_type=event_type,
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
        listing_title="Piso",
    )


def test_router_only_sends_to_enabled_channels_assigned_to_originating_search(setup):
    repository, search_id = setup
    other_search_id = repository.connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('B', 'buy') RETURNING id"
    ).fetchone()[0]
    selected = repository.create_channel("Selected", NotificationProvider.TELEGRAM)
    disabled = repository.create_channel(
        "Disabled", NotificationProvider.TELEGRAM, enabled=False
    )
    unassigned = repository.create_channel("Unassigned", NotificationProvider.TELEGRAM)
    repository.assign_channel(search_id, selected.id)
    repository.assign_channel(search_id, disabled.id)
    repository.assign_channel(other_search_id, unassigned.id)

    calls = []
    registry = NotifierRegistry()
    registry.register(
        NotificationProvider.TELEGRAM, lambda channel: RecordingNotifier(calls)
    )

    result = NotificationRouter(repository, registry).route(_event(search_id))

    assert result.selected is True
    assert [delivery.channel_id for delivery in result.deliveries] == [selected.id]
    assert calls == [_event(search_id)]


def test_router_applies_search_event_preferences_before_resolving_channels(setup):
    repository, search_id = setup
    channel = repository.create_channel("Selected", NotificationProvider.TELEGRAM)
    repository.assign_channel(search_id, channel.id)
    repository.set_event_preferences(search_id, NotificationPreferences())
    registry = NotifierRegistry()
    calls = []
    registry.register(
        NotificationProvider.TELEGRAM, lambda selected: RecordingNotifier(calls)
    )

    result = NotificationRouter(repository, registry).route(
        _event(search_id, NotificationEventType.REAPPEARANCE)
    )

    assert result.selected is False
    assert result.deliveries == ()
    assert calls == []


def test_router_isolates_unregistered_and_raising_notifiers(setup):
    repository, search_id = setup
    first = repository.create_channel("A", NotificationProvider.TELEGRAM)
    second = repository.create_channel("B", NotificationProvider.WEBHOOK)
    repository.assign_channel(search_id, first.id)
    repository.assign_channel(search_id, second.id)
    registry = NotifierRegistry()
    registry.register(
        NotificationProvider.TELEGRAM,
        lambda selected: RecordingNotifier([], error=RuntimeError("secret")),
    )

    result = NotificationRouter(repository, registry).route(_event(search_id))

    assert [delivery.result.error_category for delivery in result.deliveries] == [
        "provider_error",
        "unavailable",
    ]
    assert "secret" not in repr(result)


def test_registry_rejects_duplicate_provider_registration():
    registry = NotifierRegistry()

    def factory(channel):
        return RecordingNotifier([])

    registry.register(NotificationProvider.NTFY, factory)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(NotificationProvider.NTFY, factory)
