import json
from pathlib import Path

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.legacy_import import LegacyConfigImporter
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


def _write_config(path: Path) -> bytes:
    data = {
        "scrapy_rs_name": "Madrid alquiler",
        "time_update": "600",
        "min_price": "900",
        "max_price": "1800",
        "proxy_idealista": "on",
        "url_idealista": ["https://www.idealista.com/alquiler-viviendas/madrid/"],
        "url_pisoscom": [
            "https://www.pisos.com/alquiler/pisos-madrid/",
            "https://www.pisos.com/alquiler/pisos-getafe/",
        ],
        "telegram_chatuserID": "12345",
        "telegram_bot_token": "very-secret-token",
        "send_first": "true",
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path.read_bytes()


def test_imports_legacy_config_without_modifying_source(tmp_path: Path):
    config_file = tmp_path / "config.json"
    original = _write_config(config_file)
    with Database(tmp_path / "app.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)

        result = LegacyConfigImporter(connection).import_file(config_file)

        search = connection.execute("SELECT * FROM searches").fetchone()
        schedule = connection.execute("SELECT * FROM search_schedules").fetchone()
        portals = connection.execute(
            "SELECT * FROM search_portals ORDER BY portal_key"
        ).fetchall()
        channel = connection.execute("SELECT * FROM notification_channels").fetchone()
        assert result.imported is True
        assert search["name"] == "Madrid alquiler"
        assert search["transaction_type"] == "rent"
        assert json.loads(search["filters_json"])["min_price_euros"] == 900
        assert schedule["interval_seconds"] == 600
        assert [row["portal_key"] for row in portals] == [
            "idealista_proxy",
            "pisoscom",
        ]
        pisos_options = json.loads(portals[1]["adapter_options_json"])
        assert len(pisos_options["legacy_urls"]) == 2
        assert "very-secret-token" in channel["secret_config_json"]
        assert "very-secret-token" not in repr(result)
        assert config_file.read_bytes() == original


def test_config_import_is_idempotent(tmp_path: Path):
    config_file = tmp_path / "config.json"
    _write_config(config_file)
    with Database(tmp_path / "app.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        importer = LegacyConfigImporter(connection)

        first = importer.import_file(config_file)
        repeated = importer.import_file(config_file)

        assert first.imported is True
        assert repeated.imported is False
        assert repeated.search_id == first.search_id
        assert connection.execute("SELECT count(*) FROM searches").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM notification_channels"
        ).fetchone()[0] == 1


def test_ambiguous_transaction_is_reported_and_defaults_to_buy(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "url_pisoscom": "https://www.pisos.com/venta/pisos-madrid/",
                "url_fotocasa": "https://www.fotocasa.es/es/alquiler/viviendas/madrid",
            }
        ),
        encoding="utf-8",
    )
    with Database(tmp_path / "app.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)

        result = LegacyConfigImporter(connection).import_file(config_file)

        assert any("mix buy and rent" in warning for warning in result.warnings)
        assert connection.execute(
            "SELECT transaction_type FROM searches"
        ).fetchone()[0] == "buy"
