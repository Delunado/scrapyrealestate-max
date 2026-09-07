import json
import sqlite3
from pathlib import Path

import pytest

from scrapyrealestate.bootstrap import build_application
from scrapyrealestate.persistence.backups import (
    BackupIntegrityError,
    backup_database,
    restore_database,
)
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.runtime import RuntimePaths


def test_current_database_backup_and_restore_preserve_complete_state(tmp_path: Path):
    database_path = tmp_path / "current.sqlite3"
    backup_path = tmp_path / "backups" / "current.sqlite3"
    with Database(database_path).connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('Current', 'buy') RETURNING id"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO notification_channels (
                name, provider, config_json, secret_config_json
            ) VALUES ('Hook', 'webhook', ?, ?)
            """,
            (
                json.dumps({"endpoint_url": "https://example.invalid/hook"}),
                json.dumps({"authorization": "backup-secret"}),
            ),
        )
        assert backup_database(database_path, backup_path) == backup_path.resolve()
        connection.execute("DELETE FROM searches WHERE id = ?", (search_id,))

    assert restore_database(backup_path, database_path, replace=True) == database_path.resolve()
    with Database(database_path).connection() as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT name FROM searches").fetchone()[0] == "Current"
        assert "backup-secret" in restored.execute(
            "SELECT secret_config_json FROM notification_channels"
        ).fetchone()[0]
        assert restored.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()[0] == len(MIGRATIONS)


def test_bootstrap_snapshots_old_schema_before_migration_and_legacy_import(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "data").resolve())
    paths.ensure_data_dir()
    with Database(paths.database_file).connection() as old:
        MigrationRunner(MIGRATIONS[:3]).migrate(old)
        old.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('Before upgrade', 'rent')"
        )
    paths.config_file.write_text(
        json.dumps(
            {
                "scrapy_rs_name": "Imported legacy",
                "time_update": "600",
                "url_pisoscom": "https://www.pisos.com/alquiler/pisos-madrid/",
            }
        ),
        encoding="utf-8",
    )
    paths.ids_file.write_text("[101, 202]", encoding="utf-8")

    runtime = build_application(runtime_paths=paths)
    report = runtime.report
    assert runtime.close(grace_seconds=1)

    expected = paths.backup_dir / f"pre-migration-v3-to-v{len(MIGRATIONS)}.sqlite3"
    assert report.pre_migration_backup == expected.resolve()
    assert expected.is_file()
    with Database(expected).connection() as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 3
        assert snapshot.execute("SELECT name FROM searches").fetchone()[0] == "Before upgrade"
        assert snapshot.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'legacy_seen_ids'"
        ).fetchone() is None

    with Database(paths.database_file).connection() as upgraded:
        assert upgraded.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == len(MIGRATIONS)
        assert {row[0] for row in upgraded.execute("SELECT name FROM searches")} == {
            "Before upgrade",
            "Imported legacy",
        }
        assert upgraded.execute("SELECT count(*) FROM legacy_seen_ids").fetchone()[0] == 2

    restarted = build_application(runtime_paths=paths)
    try:
        assert restarted.report.pre_migration_backup is None
        assert tuple(paths.backup_dir.glob("pre-migration-*.sqlite3")) == (expected,)
    finally:
        assert restarted.close(grace_seconds=1)


def test_backup_and_restore_refuse_unsafe_or_corrupt_inputs(tmp_path: Path):
    database_path = tmp_path / "application.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"
    with Database(database_path).connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
    backup_database(database_path, backup_path)

    with pytest.raises(FileExistsError):
        backup_database(database_path, backup_path)
    with pytest.raises(FileExistsError):
        restore_database(backup_path, database_path)
    with pytest.raises(ValueError, match="different files"):
        restore_database(database_path, database_path, replace=True)

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises((BackupIntegrityError, sqlite3.DatabaseError)):
        restore_database(corrupt, tmp_path / "restored.sqlite3")
    assert not (tmp_path / "restored.sqlite3").exists()
