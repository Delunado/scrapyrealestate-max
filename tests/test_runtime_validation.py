import inspect

import pytest

import main
from scrapyrealestate.legacy_config import ConfigValidationError, LegacyConfig


def test_missing_portal_urls_return_structured_validation_issue():
    with pytest.raises(ConfigValidationError) as raised:
        main.get_urls(LegacyConfig())

    assert raised.value.issues == (
        main.ConfigIssue("portal_urls", "at least one portal URL is required"),
    )


def test_minimum_interval_returns_structured_validation_issue(monkeypatch):
    monkeypatch.setattr(main, "data", LegacyConfig(time_update=299))

    with pytest.raises(ConfigValidationError) as raised:
        main.checks()

    assert raised.value.issues[0].field == "time_update"


def test_missing_scrapy_project_returns_structured_validation_issue(monkeypatch):
    monkeypatch.setattr(
        main,
        "data",
        LegacyConfig(
            telegram_chatuser_id="-100123",
            telegram_bot_token="token",
            url_pisoscom=("https://www.pisos.com/search",),
        ),
    )
    monkeypatch.setattr(main.path, "exists", lambda unused_path: False)

    with pytest.raises(ConfigValidationError) as raised:
        main.check_config()

    assert raised.value.issues[0].field == "runtime_directory"


def test_missing_chat_id_returns_structured_validation_issue(monkeypatch):
    monkeypatch.setattr(
        main,
        "data",
        LegacyConfig(
            telegram_bot_token="token",
            url_pisoscom=("https://www.pisos.com/search",),
        ),
    )
    monkeypatch.setattr(main.path, "exists", lambda unused_path: True)

    with pytest.raises(ConfigValidationError) as raised:
        main.check_config()

    assert raised.value.issues[0].field == "telegram_chatuserID"


def test_legacy_runtime_has_no_direct_sys_exit_calls():
    assert "sys.exit" not in inspect.getsource(main)
