"""Notifier factory registry driven by persisted channel provider metadata."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from scrapyrealestate.notifiers.base import Notifier, NotifierConfigurationError
from scrapyrealestate.notifiers.ntfy import NtfyConfig, NtfyNotifier
from scrapyrealestate.notifiers.telegram import TelegramConfig, TelegramNotifier
from scrapyrealestate.notifiers.webhook import WebhookConfig, WebhookNotifier
from scrapyrealestate.persistence.notifications import (
    NotificationChannelDeliveryConfig,
    NotificationProvider,
)


NotifierFactory = Callable[[NotificationChannelDeliveryConfig], Notifier]


class NotifierRegistry:
    def __init__(self) -> None:
        self._factories: dict[NotificationProvider, NotifierFactory] = {}

    def register(
        self, provider: NotificationProvider, factory: NotifierFactory
    ) -> None:
        if provider in self._factories:
            raise ValueError(f"notifier provider {provider.value!r} is already registered")
        self._factories[provider] = factory

    def create(self, channel: NotificationChannelDeliveryConfig) -> Notifier:
        try:
            factory = self._factories[channel.provider]
        except KeyError as error:
            raise LookupError(
                f"notifier provider {channel.provider.value!r} is not registered"
            ) from error
        return factory(channel)


def build_default_notifier_registry() -> NotifierRegistry:
    registry = NotifierRegistry()
    registry.register(NotificationProvider.TELEGRAM, _telegram_notifier)
    registry.register(NotificationProvider.NTFY, _ntfy_notifier)
    registry.register(NotificationProvider.WEBHOOK, _webhook_notifier)
    return registry


def _telegram_notifier(channel: NotificationChannelDeliveryConfig) -> Notifier:
    return TelegramNotifier(
        TelegramConfig(
            chat_id=_required(channel.config, "chat_id", "Telegram"),
            bot_token=_required(channel.secret_config, "bot_token", "Telegram"),
        )
    )


def _ntfy_notifier(channel: NotificationChannelDeliveryConfig) -> Notifier:
    return NtfyNotifier(
        NtfyConfig(
            server_url=_required(channel.config, "server_url", "ntfy"),
            topic=_required(channel.config, "topic", "ntfy"),
            access_token=_optional(channel.secret_config, "access_token"),
            timeout_seconds=_timeout(channel.config, "ntfy"),
        )
    )


def _webhook_notifier(channel: NotificationChannelDeliveryConfig) -> Notifier:
    return WebhookNotifier(
        WebhookConfig(
            endpoint_url=_required(channel.config, "endpoint_url", "webhook"),
            authorization=_optional(channel.secret_config, "authorization"),
            timeout_seconds=_timeout(channel.config, "webhook"),
        )
    )


def _required(config: Mapping[str, Any], key: str, provider: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NotifierConfigurationError(f"{provider} {key} is required")
    return value


def _optional(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise NotifierConfigurationError(f"{key} must be text")
    return value


def _timeout(config: Mapping[str, Any], provider: str) -> float:
    value = config.get("timeout_seconds", 10.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NotifierConfigurationError(f"{provider} timeout_seconds must be numeric")
    return float(value)
