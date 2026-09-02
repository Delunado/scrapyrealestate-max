import logging
import sys

import pytest

from scrapyrealestate.legacy_config import ConfigValidationError, LegacyConfig
from scrapyrealestate.security import (
    REDACTED,
    SecretRedactionFilter,
    SecretRedactingFormatter,
    resolve_telegram_bot_token,
)


def test_explicit_configured_telegram_token_is_used():
    config = LegacyConfig(telegram_bot_token="configured-secret")

    assert resolve_telegram_bot_token(config, {}) == "configured-secret"


def test_environment_telegram_token_overrides_configured_token():
    config = LegacyConfig(telegram_bot_token="configured-secret")

    token = resolve_telegram_bot_token(config, {"TELEGRAM_BOT_TOKEN": "env-secret"})

    assert token == "env-secret"


def test_missing_telegram_token_is_a_structured_configuration_error():
    with pytest.raises(ConfigValidationError) as raised:
        resolve_telegram_bot_token(LegacyConfig(), {})

    assert raised.value.issues[0].field == "telegram_bot_token"
    assert "TELEGRAM_BOT_TOKEN" in raised.value.issues[0].message


def test_legacy_config_representation_does_not_contain_token():
    config = LegacyConfig(telegram_bot_token="configured-secret")

    assert "configured-secret" not in repr(config)
    assert "configured-secret" not in str(config)


def test_logging_filter_redacts_secrets_from_messages_and_arguments():
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request %s failed with configured-secret",
        args=("env-secret",),
        exc_info=None,
    )

    assert SecretRedactionFilter(
        ("configured-secret", "env-secret")
    ).filter(record)

    assert record.getMessage() == f"request {REDACTED} failed with {REDACTED}"


def test_logging_formatter_redacts_secrets_from_tracebacks():
    try:
        raise RuntimeError("request containing configured-secret failed")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="delivery failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    formatted = SecretRedactingFormatter(
        "%(levelname)s %(message)s", secrets=("configured-secret",)
    ).format(record)

    assert "configured-secret" not in formatted
    assert REDACTED in formatted
