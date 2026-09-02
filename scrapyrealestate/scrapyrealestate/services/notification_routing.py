"""Route provider-neutral events to their search's selected channels."""

from __future__ import annotations

from dataclasses import dataclass

from scrapyrealestate.domain.notification import NotificationEvent
from scrapyrealestate.notifiers.base import DeliveryResult
from scrapyrealestate.notifiers.registry import NotifierRegistry
from scrapyrealestate.persistence.notifications import (
    NotificationProvider,
    NotificationRepository,
)


@dataclass(frozen=True, slots=True)
class RoutedDelivery:
    channel_id: int
    channel_name: str
    provider: NotificationProvider
    result: DeliveryResult


@dataclass(frozen=True, slots=True)
class RoutingResult:
    event_id: int
    selected: bool
    deliveries: tuple[RoutedDelivery, ...] = ()


class NotificationRouter:
    def __init__(
        self,
        repository: NotificationRepository,
        registry: NotifierRegistry,
    ) -> None:
        self._repository = repository
        self._registry = registry

    def route(self, event: NotificationEvent) -> RoutingResult:
        preferences = self._repository.preferences_for_search(event.search_id)
        if not preferences.is_enabled(event.event_type):
            return RoutingResult(event.id, selected=False)

        deliveries: list[RoutedDelivery] = []
        for channel in self._repository.delivery_channels_for_search(event.search_id):
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
            deliveries.append(
                RoutedDelivery(channel.id, channel.name, channel.provider, result)
            )
        return RoutingResult(event.id, selected=True, deliveries=tuple(deliveries))
