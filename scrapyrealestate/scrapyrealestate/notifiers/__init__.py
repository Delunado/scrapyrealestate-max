"""Notification provider interfaces and implementations."""

from scrapyrealestate.notifiers.base import (
    DeliveryResult,
    Notifier,
    NotifierConfigurationError,
)
from scrapyrealestate.notifiers.formatting import NotificationMessage, format_notification
from scrapyrealestate.notifiers.ntfy import NtfyConfig, NtfyNotifier
from scrapyrealestate.notifiers.registry import (
    NotifierRegistry,
    build_default_notifier_registry,
)
from scrapyrealestate.notifiers.telegram import TelegramConfig, TelegramNotifier
from scrapyrealestate.notifiers.webhook import (
    WEBHOOK_SCHEMA_VERSION,
    WebhookConfig,
    WebhookNotifier,
    build_webhook_payload,
)

__all__ = [
    "DeliveryResult",
    "NotificationMessage",
    "Notifier",
    "NotifierConfigurationError",
    "NotifierRegistry",
    "NtfyConfig",
    "NtfyNotifier",
    "TelegramConfig",
    "TelegramNotifier",
    "WEBHOOK_SCHEMA_VERSION",
    "WebhookConfig",
    "WebhookNotifier",
    "build_webhook_payload",
    "build_default_notifier_registry",
    "format_notification",
]
