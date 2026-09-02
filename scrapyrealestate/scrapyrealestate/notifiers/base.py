"""Contract implemented by every notification provider."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from typing import Protocol

from scrapyrealestate.domain.notification import NotificationEvent
from scrapyrealestate.security import redact_secrets


class NotifierConfigurationError(ValueError):
    """A channel cannot be constructed from its user-supplied configuration."""


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """A notifier outcome that can be persisted without raising provider errors."""

    success: bool
    provider_message_id: str | None = None
    error_category: str | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.success and (self.error_category is not None or self.diagnostic is not None):
            raise ValueError("a successful delivery cannot contain error details")
        if not self.success and not self.error_category:
            raise ValueError("a failed delivery requires an error category")

    @classmethod
    def delivered(cls, provider_message_id: str | None = None) -> DeliveryResult:
        return cls(True, provider_message_id=provider_message_id)

    @classmethod
    def failed(cls, error_category: str, diagnostic: str | None = None) -> DeliveryResult:
        return cls(False, error_category=error_category, diagnostic=diagnostic)


class Notifier(Protocol):
    """Synchronous delivery boundary used by the notification router."""

    def send(self, event: NotificationEvent) -> DeliveryResult:
        """Deliver one event and return a classified, non-secret outcome."""


def redact_delivery_result(
    result: DeliveryResult, secrets: Iterable[str]
) -> DeliveryResult:
    """Defensively sanitize every provider-controlled status field."""
    configured = tuple(secrets)

    def clean(value: str | None) -> str | None:
        return redact_secrets(value, configured) if value is not None else None

    return DeliveryResult(
        success=result.success,
        provider_message_id=clean(result.provider_message_id),
        error_category=clean(result.error_category),
        diagnostic=clean(result.diagnostic),
    )
