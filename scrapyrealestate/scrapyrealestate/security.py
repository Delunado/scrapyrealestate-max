"""Secret resolution and redaction helpers for legacy runtime boundaries."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from typing import Any

from scrapyrealestate.legacy_config import (
    ConfigIssue,
    ConfigValidationError,
    LegacyConfig,
)


TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
REDACTED = "[REDACTED]"


def resolve_telegram_bot_token(
    config: LegacyConfig, environ: Mapping[str, str] | None = None
) -> str:
    """Return the explicit Telegram token, preferring an environment override."""
    environment = os.environ if environ is None else environ
    environment_token = environment.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    token = environment_token or config.telegram_bot_token.strip()
    if not token:
        raise ConfigValidationError(
            [
                ConfigIssue(
                    "telegram_bot_token",
                    f"is required in config.json or {TELEGRAM_BOT_TOKEN_ENV}",
                )
            ]
        )
    return token


def configured_telegram_secrets(
    config: LegacyConfig, environ: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Return every configured token so overridden values are also redacted."""
    environment = os.environ if environ is None else environ
    return tuple(
        secret
        for secret in (
            config.telegram_bot_token.strip(),
            environment.get(TELEGRAM_BOT_TOKEN_ENV, "").strip(),
        )
        if secret
    )


def configured_notification_secrets(
    *secret_configs: Mapping[str, Any],
) -> tuple[str, ...]:
    """Flatten provider secret mappings for status/log redaction."""
    found: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value:
            found.append(value)

    for config in secret_configs:
        visit(config)
    return tuple(dict.fromkeys(found))


def redact_secrets(value: object, secrets: Iterable[str]) -> str:
    redacted = str(value)
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    return redacted


class SecretRedactionFilter(logging.Filter):
    """Mask configured secrets before a log record reaches a handler."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage(), self._secrets)
        record.args = ()
        return True


class SecretRedactingFormatter(logging.Formatter):
    """Redact secrets from the complete formatted record, including tracebacks."""

    def __init__(self, *args, secrets: Iterable[str], **kwargs):
        super().__init__(*args, **kwargs)
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record), self._secrets)
