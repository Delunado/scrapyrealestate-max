"""Typed loading and validation for the legacy JSON configuration file."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PORTAL_URL_FIELDS = (
    "url_idealista",
    "url_pisoscom",
    "url_fotocasa",
    "url_habitaclia",
    "url_yaencontre",
)
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    field: str
    message: str


class ConfigValidationError(ValueError):
    """A configuration error containing safe, field-specific issues."""

    def __init__(self, issues: list[ConfigIssue] | tuple[ConfigIssue, ...]):
        self.issues = tuple(issues)
        details = "; ".join(f"{issue.field}: {issue.message}" for issue in self.issues)
        super().__init__(f"Invalid legacy configuration: {details}")


@dataclass(frozen=True, slots=True)
class LegacyConfig:
    scrapy_rs_name: str = "scrapyrealestate"
    log_level: str = "INFO"
    log_level_scrapy: str = "WARNING"
    time_update: int = 900
    telegram_chatuser_id: str = ""
    telegram_bot_token: str = field(default="", repr=False)
    start_msg: bool = True
    min_price: int = 0
    max_price: int = 0
    proxy_idealista: bool = False
    send_first: bool = False
    url_idealista: tuple[str, ...] = ()
    url_pisoscom: tuple[str, ...] = ()
    url_fotocasa: tuple[str, ...] = ()
    url_habitaclia: tuple[str, ...] = ()
    url_yaencontre: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> LegacyConfig:
        issues: list[ConfigIssue] = []
        defaults = cls()

        name = _string_value(raw, "scrapy_rs_name", defaults.scrapy_rs_name, issues)
        if not name:
            issues.append(ConfigIssue("scrapy_rs_name", "must not be empty"))

        log_level = _log_level(raw, "log_level", defaults.log_level, issues)
        scrapy_log = _log_level(
            raw, "log_level_scrapy", defaults.log_level_scrapy, issues
        )
        interval = _integer_value(raw, "time_update", defaults.time_update, issues)
        min_price = _integer_value(raw, "min_price", defaults.min_price, issues)
        max_price = _integer_value(raw, "max_price", defaults.max_price, issues)

        if interval < 300:
            issues.append(ConfigIssue("time_update", "must be at least 300 seconds"))
        if min_price < 0:
            issues.append(ConfigIssue("min_price", "must be zero or greater"))
        if max_price < 0:
            issues.append(ConfigIssue("max_price", "must be zero or greater"))
        if max_price and max_price < min_price:
            issues.append(ConfigIssue("max_price", "must be zero or at least min_price"))

        url_values = {
            field: _url_list(raw, field, issues) for field in PORTAL_URL_FIELDS
        }
        config = cls(
            scrapy_rs_name=name,
            log_level=log_level,
            log_level_scrapy=scrapy_log,
            time_update=interval,
            telegram_chatuser_id=_string_value(
                raw, "telegram_chatuserID", defaults.telegram_chatuser_id, issues
            ),
            telegram_bot_token=_string_value(
                raw, "telegram_bot_token", defaults.telegram_bot_token, issues
            ),
            start_msg=_boolean_value(raw, "start_msg", defaults.start_msg, issues),
            min_price=min_price,
            max_price=max_price,
            proxy_idealista=_boolean_value(
                raw, "proxy_idealista", defaults.proxy_idealista, issues
            ),
            send_first=_boolean_value(raw, "send_first", defaults.send_first, issues),
            **url_values,
        )
        if issues:
            raise ConfigValidationError(issues)
        return config

    @property
    def portal_urls(self) -> dict[str, tuple[str, ...]]:
        return {field: getattr(self, field) for field in PORTAL_URL_FIELDS}


def load_legacy_config(config_file: Path) -> LegacyConfig:
    """Read a legacy JSON file and return a validated typed configuration."""
    try:
        with config_file.open(encoding="utf-8") as file:
            raw = json.load(file)
    except FileNotFoundError as error:
        raise ConfigValidationError(
            [ConfigIssue("config_file", "does not exist")]
        ) from error
    except (OSError, UnicodeError) as error:
        raise ConfigValidationError(
            [ConfigIssue("config_file", "could not be read")]
        ) from error
    except json.JSONDecodeError as error:
        raise ConfigValidationError(
            [ConfigIssue("config_file", "must contain valid JSON")]
        ) from error
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(
            [ConfigIssue("config_file", "top-level value must be an object")]
        )
    return LegacyConfig.from_mapping(raw)


def _string_value(
    raw: Mapping[str, Any],
    field: str,
    default: str,
    issues: list[ConfigIssue],
) -> str:
    value = raw.get(field, default)
    if value is None:
        return ""
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip()
    issues.append(ConfigIssue(field, "must be text"))
    return default


def _integer_value(
    raw: Mapping[str, Any],
    field: str,
    default: int,
    issues: list[ConfigIssue],
) -> int:
    value = raw.get(field, default)
    if isinstance(value, bool):
        issues.append(ConfigIssue(field, "must be an integer"))
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        issues.append(ConfigIssue(field, "must be an integer"))
        return default


def _boolean_value(
    raw: Mapping[str, Any],
    field: str,
    default: bool,
    issues: list[ConfigIssue],
) -> bool:
    value = raw.get(field, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "on", "yes", "1"}:
            return True
        if normalized in {"false", "off", "no", "0"}:
            return False
    issues.append(ConfigIssue(field, "must be a boolean"))
    return default


def _log_level(
    raw: Mapping[str, Any],
    field: str,
    default: str,
    issues: list[ConfigIssue],
) -> str:
    value = _string_value(raw, field, default, issues).upper()
    if value not in LOG_LEVELS:
        allowed = ", ".join(sorted(LOG_LEVELS))
        issues.append(ConfigIssue(field, f"must be one of {allowed}"))
        return default
    return value


def _url_list(
    raw: Mapping[str, Any], field: str, issues: list[ConfigIssue]
) -> tuple[str, ...]:
    value = raw.get(field, ())
    if value is None or value == "":
        return ()
    values = (value,) if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        issues.append(ConfigIssue(field, "must be a URL or a list of URLs"))
        return ()
    if not all(isinstance(item, str) for item in values):
        issues.append(ConfigIssue(field, "must contain only text URLs"))
        return ()
    return tuple(item.strip() for item in values if item.strip())
