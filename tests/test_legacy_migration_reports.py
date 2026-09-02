import json
from pathlib import Path

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.import_reports import LegacyImportReportRepository
from scrapyrealestate.persistence.legacy_import import LegacyConfigImporter
from scrapyrealestate.persistence.legacy_seen import LegacySeenRepository
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


def test_full_legacy_import_reports_preserved_sources_and_is_idempotent(
    tmp_path: Path,
):
    config_file = tmp_path / "config.json"
    ids_file = tmp_path / "ids.json"
    config_file.write_text(
        json.dumps(
            {
                "scrapy_rs_name": "Legacy",
                "url_pisoscom": "https://www.pisos.com/venta/pisos-madrid/",
                "telegram_chatuserID": "123",
                "telegram_bot_token": "secret-token",
            }
        ),
        encoding="utf-8",
    )
    ids_file.write_text("[10, 20, 30]", encoding="utf-8")
    original_config = config_file.read_bytes()
    original_ids = ids_file.read_bytes()

    with Database(tmp_path / "app.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        config_importer = LegacyConfigImporter(connection)
        seen_importer = LegacySeenRepository(connection)

        config_importer.import_file(config_file)
        seen_importer.import_file(ids_file)
        config_importer.import_file(config_file)
        seen_importer.import_file(ids_file)

        reports = LegacyImportReportRepository(connection).list()
        assert len(reports) == 2
        assert all(report.source_preserved for report in reports)
        assert all(
            report.rollback_source_marker
            == f"preserved-source-sha256:{report.source_digest}"
            for report in reports
        )
        assert connection.execute("SELECT count(*) FROM searches").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM notification_channels"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM legacy_seen_ids"
        ).fetchone()[0] == 3
        assert config_file.read_bytes() == original_config
        assert ids_file.read_bytes() == original_ids
        assert "secret-token" not in repr(reports)
