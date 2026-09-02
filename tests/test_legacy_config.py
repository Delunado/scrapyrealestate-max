import json
from pathlib import Path

import pytest

from scrapyrealestate.legacy_config import (
    ConfigValidationError,
    LegacyConfig,
    load_legacy_config,
)


def test_legacy_config_converts_string_values_and_url_lists(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "scrapy_rs_name": "home_search",
                "log_level": "debug",
                "log_level_scrapy": "error",
                "time_update": "600",
                "telegram_chatuserID": -100123,
                "telegram_bot_token": "secret-token",
                "start_msg": "False",
                "min_price": "100000",
                "max_price": "250000",
                "proxy_idealista": "on",
                "send_first": "True",
                "url_idealista": ["https://www.idealista.com/search", ""],
                "url_pisoscom": "https://www.pisos.com/search",
            }
        ),
        encoding="utf-8",
    )

    config = load_legacy_config(config_file)

    assert config == LegacyConfig(
        scrapy_rs_name="home_search",
        log_level="DEBUG",
        log_level_scrapy="ERROR",
        time_update=600,
        telegram_chatuser_id="-100123",
        telegram_bot_token="secret-token",
        start_msg=False,
        min_price=100000,
        max_price=250000,
        proxy_idealista=True,
        send_first=True,
        url_idealista=("https://www.idealista.com/search",),
        url_pisoscom=("https://www.pisos.com/search",),
    )


def test_legacy_config_has_compatibility_defaults():
    assert LegacyConfig.from_mapping({}) == LegacyConfig()


def test_legacy_config_collects_field_validation_errors():
    with pytest.raises(ConfigValidationError) as raised:
        LegacyConfig.from_mapping(
            {
                "scrapy_rs_name": "",
                "log_level": "verbose",
                "time_update": "soon",
                "min_price": -1,
                "max_price": [],
                "start_msg": "sometimes",
                "url_pisoscom": ["https://example.test", 42],
            }
        )

    fields = {issue.field for issue in raised.value.issues}
    assert fields == {
        "scrapy_rs_name",
        "log_level",
        "time_update",
        "min_price",
        "max_price",
        "start_msg",
        "url_pisoscom",
    }


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("not-json", "must contain valid JSON"),
        ("[]", "top-level value must be an object"),
    ],
)
def test_legacy_config_reports_file_errors(
    tmp_path: Path, contents: str, message: str
):
    config_file = tmp_path / "config.json"
    config_file.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigValidationError, match=message):
        load_legacy_config(config_file)


def test_legacy_config_reports_missing_file(tmp_path: Path):
    with pytest.raises(ConfigValidationError, match="does not exist"):
        load_legacy_config(tmp_path / "missing.json")
