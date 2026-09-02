"""Notification provider interfaces and implementations."""

from scrapyrealestate.notifiers.base import (
    DeliveryResult,
    Notifier,
    NotifierConfigurationError,
)
from scrapyrealestate.notifiers.formatting import NotificationMessage, format_notification
from scrapyrealestate.notifiers.ntfy import NtfyConfig, NtfyNotifier
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
    "NtfyConfig",
    "NtfyNotifier",
    "TelegramConfig",
    "TelegramNotifier",
    "WEBHOOK_SCHEMA_VERSION",
    "WebhookConfig",
    "WebhookNotifier",
    "build_webhook_payload",
    "format_notification",
]
