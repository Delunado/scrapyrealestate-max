"""Durable notification dispatch with bounded retries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from scrapyrealestate.notifiers.base import DeliveryResult, redact_delivery_result
from scrapyrealestate.notifiers.registry import NotifierRegistry
from scrapyrealestate.persistence.notifications import (
    ClaimedDelivery,
    DeliveryCompletion,
    NotificationRepository,
)
from scrapyrealestate.security import configured_notification_secrets


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    max_attempts: int = 3
    base_backoff_seconds: float = 30.0
    max_backoff_seconds: float = 900.0
    lease_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 10
        ):
            raise ValueError("max_attempts must be between 1 and 10")
        for value in (self.base_backoff_seconds, self.max_backoff_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("retry backoff values must be numeric")
        if self.base_backoff_seconds <= 0 or self.max_backoff_seconds <= 0:
            raise ValueError("retry backoff values must be positive")
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("base backoff cannot exceed maximum backoff")
        if (
            isinstance(self.lease_seconds, bool)
            or not isinstance(self.lease_seconds, (int, float))
            or not 0 < self.lease_seconds <= 3600
        ):
            raise ValueError("lease_seconds must be between 0 and 3600")


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    claim: ClaimedDelivery
    result: DeliveryResult
    completion: DeliveryCompletion


class DurableNotificationDispatcher:
    def __init__(
        self,
        repository: NotificationRepository,
        registry: NotifierRegistry,
        *,
        policy: DeliveryPolicy = DeliveryPolicy(),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def enqueue(self, event_id: int) -> int:
        """Idempotently create first attempts for currently eligible channels."""
        return len(
            self._repository.ensure_event_deliveries(
                event_id, available_at=self._clock()
            )
        )

    def dispatch_next(self) -> DispatchOutcome | None:
        claim = self._repository.claim_next_delivery(
            self._clock(), lease_seconds=self._policy.lease_seconds
        )
        if claim is None:
            return None

        secrets: tuple[str, ...] = ()
        try:
            event = self._repository.event_for_delivery(claim.attempt.event_id)
            channel = self._repository.delivery_channel(claim.attempt.channel_id)
            secrets = configured_notification_secrets(channel.secret_config)
        except Exception:
            result = DeliveryResult.failed(
                "event_error", "notification delivery data is invalid"
            )
        else:
            try:
                notifier = self._registry.create(channel)
            except LookupError:
                result = DeliveryResult.failed(
                    "unavailable", "notification provider is not registered"
                )
            except Exception:
                result = DeliveryResult.failed(
                    "configuration_error", "notification channel is invalid"
                )
            else:
                try:
                    result = notifier.send(event)
                except Exception:
                    result = DeliveryResult.failed(
                        "provider_error", "notification delivery failed"
                    )

        result = redact_delivery_result(result, secrets)
        completion = self._repository.complete_delivery(
            claim,
            success=result.success,
            completed_at=self._clock(),
            error_category=result.error_category,
            diagnostic=result.diagnostic,
            provider_message_id=result.provider_message_id,
            max_attempts=self._policy.max_attempts,
            base_backoff_seconds=self._policy.base_backoff_seconds,
            max_backoff_seconds=self._policy.max_backoff_seconds,
        )
        return DispatchOutcome(claim, result, completion)

    def dispatch_available(self, *, limit: int = 100) -> tuple[DispatchOutcome, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        outcomes = []
        while len(outcomes) < limit:
            outcome = self.dispatch_next()
            if outcome is None:
                break
            outcomes.append(outcome)
        return tuple(outcomes)
