"""Notification provider interfaces and implementations."""

from scrapyrealestate.notifiers.base import (
    DeliveryResult,
    Notifier,
    NotifierConfigurationError,
)
from scrapyrealestate.notifiers.formatting import NotificationMessage, format_notification
from scrapyrealestate.notifiers.telegram import TelegramConfig, TelegramNotifier

__all__ = [
    "DeliveryResult",
    "NotificationMessage",
    "Notifier",
    "NotifierConfigurationError",
    "TelegramConfig",
    "TelegramNotifier",
    "format_notification",
]
