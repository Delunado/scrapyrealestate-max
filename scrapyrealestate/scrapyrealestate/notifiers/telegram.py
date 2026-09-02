"""Telegram delivery behind the provider-neutral notifier boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import telebot

from scrapyrealestate.domain.notification import NotificationEvent
from scrapyrealestate.notifiers.base import (
    DeliveryResult,
    NotifierConfigurationError,
)
from scrapyrealestate.notifiers.formatting import format_notification


class TelegramClient(Protocol):
    def send_message(self, chat_id: str, text: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    chat_id: str
    bot_token: str = field(repr=False)

    def __post_init__(self) -> None:
        chat_id = self.chat_id.strip()
        token = self.bot_token.strip()
        if not chat_id:
            raise NotifierConfigurationError("Telegram chat_id is required")
        if not token:
            raise NotifierConfigurationError("Telegram bot_token is required")
        object.__setattr__(self, "chat_id", chat_id)
        object.__setattr__(self, "bot_token", token)


class TelegramNotifier:
    """Send shared plain-text messages using one user's Telegram bot."""

    def __init__(
        self,
        config: TelegramConfig,
        *,
        client: TelegramClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or telebot.TeleBot(config.bot_token)

    def send(self, event: NotificationEvent) -> DeliveryResult:
        message = format_notification(event)
        try:
            response = self._client.send_message(self._config.chat_id, message.text)
        except TimeoutError:
            return DeliveryResult.failed("timeout", "Telegram request timed out")
        except Exception:  # provider/client exceptions must not escape the boundary
            return DeliveryResult.failed("provider_error", "Telegram delivery failed")
        message_id = getattr(response, "message_id", None)
        return DeliveryResult.delivered(
            str(message_id) if message_id is not None else None
        )
