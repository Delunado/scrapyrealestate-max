from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scrapyrealestate.domain.notification import (
    NotificationEventType,
    NotificationPreferences,
)
from scrapyrealestate.notifiers import DeliveryResult, NotifierRegistry
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import (
    DeliveryStatus,
    NotificationProvider,
    NotificationRepository,
    StaleDeliveryClaimError,
)
from scrapyrealestate.services.notification_delivery import (
    DeliveryPolicy,
    DurableNotificationDispatcher,
)


UTC = timezone.utc


class StubNotifier:
    def __init__(self, calls, result):
        self.calls = calls
        self.result = result

    def send(self, event):
        self.calls.append(event)
        return self.result


@pytest.fixture
def setup(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('Centro', 'buy') RETURNING id"
        ).fetchone()[0]
        listing_id = connection.execute(
            """
            INSERT INTO listings (
                portal_key, external_id, canonical_url, transaction_type, title,
                price_euros, area_sqm, rooms, location, first_seen_at, last_seen_at
            ) VALUES ('pisoscom', '123', 'https://example.com/123', 'buy',
                      'Piso', 190000, 80, 3, 'Madrid',
                      '2026-09-01T10:00:00Z', '2026-09-01T10:00:00Z')
            RETURNING id
            """
        ).fetchone()[0]
        repository = NotificationRepository(connection)
        channel = repository.create_channel(
            "Telegram",
            NotificationProvider.TELEGRAM,
            config={"chat_id": "123"},
            secret_config={"bot_token": "secret"},
        )
        repository.assign_channel(search_id, channel.id)
        event = repository.create_event(
            search_id,
            NotificationEventType.NEW_LISTING,
            "new:pisoscom:123",
            datetime(2026, 9, 2, 10, tzinfo=UTC),
            listing_id=listing_id,
            payload={"price_euros": 180_000},
        ).event
        yield repository, search_id, channel.id, event.id


def test_event_delivery_creation_is_preference_aware_and_idempotent(setup):
    repository, search_id, channel_id, event_id = setup
    first = repository.ensure_event_deliveries(event_id)
    second = repository.ensure_event_deliveries(event_id)

    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert first[0].channel_id == channel_id

    repository.set_event_preferences(
        search_id, NotificationPreferences(new_listing=False)
    )
    assert repository.ensure_event_deliveries(event_id) == ()


def test_claim_is_exclusive_until_lease_expiry_and_then_reclaimable(setup):
    repository, _, _, event_id = setup
    now = datetime(2026, 9, 2, 10, 1, tzinfo=UTC)
    repository.ensure_event_deliveries(event_id, available_at=now)

    first = repository.claim_next_delivery(
        now, lease_seconds=60, claim_token="worker-one"
    )
    assert first is not None
    assert first.attempt.status is DeliveryStatus.CLAIMED
    assert repository.claim_next_delivery(now + timedelta(seconds=59)) is None

    reclaimed = repository.claim_next_delivery(
        now + timedelta(seconds=60), claim_token="worker-two"
    )
    assert reclaimed is not None
    assert reclaimed.attempt.id == first.attempt.id
    assert "worker-two" not in repr(reclaimed)
    with pytest.raises(StaleDeliveryClaimError):
        repository.complete_delivery(
            first, success=True, completed_at=now + timedelta(seconds=61)
        )


def test_success_is_recorded_and_never_claimed_again_after_restart(setup):
    repository, _, _, event_id = setup
    now = datetime(2026, 9, 2, 10, 1, tzinfo=UTC)
    repository.ensure_event_deliveries(event_id, available_at=now)
    claim = repository.claim_next_delivery(now, claim_token="worker")

    completion = repository.complete_delivery(
        claim,
        success=True,
        completed_at=now + timedelta(seconds=1),
        provider_message_id="message-1",
    )

    assert completion.attempt.status is DeliveryStatus.SUCCEEDED
    assert completion.attempt.provider_message_id == "message-1"
    assert completion.retry is None
    database_path = Path(
        repository.connection.execute("PRAGMA database_list").fetchone()["file"]
    )
    with Database(database_path).connection() as restarted_connection:
        restarted = NotificationRepository(restarted_connection)
        assert restarted.claim_next_delivery(now + timedelta(days=1)) is None
        assert len(restarted.ensure_event_deliveries(event_id)) == 1


def test_failures_schedule_bounded_exponential_retries(setup):
    repository, _, _, event_id = setup
    now = datetime(2026, 9, 2, 10, 1, tzinfo=UTC)
    repository.ensure_event_deliveries(event_id, available_at=now)

    first = repository.claim_next_delivery(now, claim_token="one")
    completed_first = repository.complete_delivery(
        first,
        success=False,
        completed_at=now,
        error_category="timeout",
        diagnostic="x" * 3000,
        max_attempts=3,
        base_backoff_seconds=10,
        max_backoff_seconds=15,
    )
    assert completed_first.attempt.status is DeliveryStatus.FAILED
    assert len(completed_first.attempt.redacted_diagnostic) == 2000
    assert completed_first.retry.attempt_number == 2
    assert repository.claim_next_delivery(now + timedelta(seconds=9)) is None

    second = repository.claim_next_delivery(
        now + timedelta(seconds=10), claim_token="two"
    )
    completed_second = repository.complete_delivery(
        second,
        success=False,
        completed_at=now + timedelta(seconds=10),
        error_category="transport_error",
        max_attempts=3,
        base_backoff_seconds=10,
        max_backoff_seconds=15,
    )
    assert completed_second.retry.attempt_number == 3
    assert repository.claim_next_delivery(now + timedelta(seconds=24)) is None

    third = repository.claim_next_delivery(
        now + timedelta(seconds=25), claim_token="three"
    )
    completed_third = repository.complete_delivery(
        third,
        success=False,
        completed_at=now + timedelta(seconds=25),
        error_category="provider_error",
        max_attempts=3,
        base_backoff_seconds=10,
        max_backoff_seconds=15,
    )
    assert completed_third.retry is None
    assert repository.claim_next_delivery(now + timedelta(days=1)) is None


def test_dispatcher_hydrates_sends_and_records_delivery(setup):
    repository, _, channel_id, event_id = setup
    now = datetime(2026, 9, 2, 10, 1, tzinfo=UTC)
    calls = []
    registry = NotifierRegistry()
    registry.register(
        NotificationProvider.TELEGRAM,
        lambda channel: StubNotifier(calls, DeliveryResult.delivered("sent-1")),
    )
    dispatcher = DurableNotificationDispatcher(
        repository,
        registry,
        clock=lambda: now,
    )

    assert dispatcher.enqueue(event_id) == 1
    outcome = dispatcher.dispatch_next()

    assert outcome.result.success is True
    assert outcome.claim.attempt.channel_id == channel_id
    assert outcome.completion.attempt.status is DeliveryStatus.SUCCEEDED
    assert calls[0].price_euros == 180_000
    assert calls[0].listing_title == "Piso"
    assert dispatcher.dispatch_next() is None


def test_dispatcher_records_safe_failure_and_retry(setup):
    repository, _, _, event_id = setup
    now = datetime(2026, 9, 2, 10, 1, tzinfo=UTC)
    registry = NotifierRegistry()
    dispatcher = DurableNotificationDispatcher(
        repository,
        registry,
        policy=DeliveryPolicy(base_backoff_seconds=5, max_backoff_seconds=5),
        clock=lambda: now,
    )
    dispatcher.enqueue(event_id)

    outcome = dispatcher.dispatch_next()

    assert outcome.result.error_category == "unavailable"
    assert outcome.completion.attempt.status is DeliveryStatus.FAILED
    assert outcome.completion.retry.attempt_number == 2
    assert "secret" not in repr(outcome)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_attempts": 0},
        {"max_attempts": 2.5},
        {"base_backoff_seconds": 0},
        {"max_backoff_seconds": "30"},
        {"base_backoff_seconds": 31, "max_backoff_seconds": 30},
        {"lease_seconds": 0},
        {"lease_seconds": True},
    ],
)
def test_delivery_policy_is_strictly_bounded(changes):
    with pytest.raises(ValueError):
        DeliveryPolicy(**changes)
