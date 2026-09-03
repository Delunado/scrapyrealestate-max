"""Validated notification-channel configuration and safe test delivery."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from scrapyrealestate.domain.notification import NotificationEvent, NotificationEventType
from scrapyrealestate.notifiers.base import (
    DeliveryResult,
    NotifierConfigurationError,
    redact_delivery_result,
)
from scrapyrealestate.notifiers.registry import NotifierRegistry
from scrapyrealestate.persistence.notifications import (
    NotificationChannelDeliveryConfig,
    NotificationChannelRecord,
    NotificationChannelTestRecord,
    NotificationProvider,
    NotificationRepository,
)
from scrapyrealestate.security import configured_notification_secrets


class NotificationChannelConfigurationService:
    """Keep raw credentials out of web handlers and template contexts."""

    def __init__(
        self, repository: NotificationRepository, registry: NotifierRegistry
    ) -> None:
        self._repository = repository
        self._registry = registry

    def create(
        self,
        *,
        name: str,
        provider: NotificationProvider,
        config: dict[str, Any],
        secret_config: dict[str, Any],
        enabled: bool,
    ) -> NotificationChannelRecord:
        candidate = NotificationChannelDeliveryConfig(
            id=1,
            name=_name(name),
            provider=provider,
            config=config,
            secret_config=secret_config,
        )
        self._registry.create(candidate)
        return self._repository.create_channel(
            candidate.name,
            provider,
            config=config,
            secret_config=secret_config,
            enabled=enabled,
        )

    def update(
        self,
        channel_id: int,
        *,
        name: str,
        config: dict[str, Any],
        submitted_secrets: dict[str, Any],
        enabled: bool,
    ) -> NotificationChannelRecord:
        existing = self._repository.delivery_channel(channel_id)
        merged_secrets = dict(existing.secret_config)
        merged_secrets.update(
            {key: value for key, value in submitted_secrets.items() if value not in (None, "")}
        )
        candidate = NotificationChannelDeliveryConfig(
            id=existing.id,
            name=_name(name),
            provider=existing.provider,
            config=config,
            secret_config=merged_secrets,
        )
        self._registry.create(candidate)
        return self._repository.update_channel(
            channel_id,
            name=candidate.name,
            config=config,
            secret_config=merged_secrets,
            enabled=enabled,
        )

    def send_test(self, channel_id: int) -> NotificationChannelTestRecord:
        channel = self._repository.delivery_channel(channel_id)
        secrets = configured_notification_secrets(channel.secret_config)
        try:
            notifier = self._registry.create(channel)
            result = notifier.send(
                NotificationEvent(
                    id=1,
                    search_id=1,
                    search_name="Prueba de ScrapyRealEstate",
                    event_type=NotificationEventType.NEW_LISTING,
                    occurred_at=datetime.now(timezone.utc),
                    listing_title="Notificación de prueba",
                )
            )
            result = redact_delivery_result(result, secrets)
        except NotifierConfigurationError:
            result = DeliveryResult.failed(
                "configuration_error", "La configuración del canal no es válida"
            )
        except Exception:
            result = DeliveryResult.failed(
                "provider_error", "No se pudo enviar la notificación de prueba"
            )
        return self._repository.record_channel_test(
            channel_id,
            success=result.success,
            tested_at=datetime.now(timezone.utc),
            error_category=result.error_category,
            diagnostic=result.diagnostic,
        )


def _name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("El nombre es obligatorio")
    return name
