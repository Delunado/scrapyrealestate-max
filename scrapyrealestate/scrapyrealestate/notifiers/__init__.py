"""Notification provider interfaces and implementations."""

from scrapyrealestate.notifiers.base import (
    DeliveryResult,
    Notifier,
    NotifierConfigurationError,
)
from scrapyrealestate.notifiers.formatting import NotificationMessage, format_notification
from scrapyrealestate.notifiers.ntfy import NtfyConfig, NtfyNotifier
from scrapyrealestate.notifiers.telegram import TelegramConfig, TelegramNotifier

__all__ = [
    "DeliveryResult",
    "NotificationMessage",
    "Notifier",
    "NotifierConfigurationError",
    "NtfyConfig",
    "NtfyNotifier",
    "TelegramConfig",
    "TelegramNotifier",
    "format_notification",
]
