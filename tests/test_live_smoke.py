import sqlite3
from pathlib import Path

import pytest

from scrapyrealestate.live_smoke import enabled_portals, main
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.runtime import DATA_DIR_ENV


def test_enabled_portals_are_discovered_without_configuration_details(tmp_path: Path):
    database_path = tmp_path / "data" / "scrapyrealestate.sqlite3"
    with Database(database_path).connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('Live', 'rent') RETURNING id"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (?, 600)",
            (search_id,),
        )
        connection.executemany(
            "INSERT INTO search_portals (search_id, portal_key, enabled) VALUES (?, ?, ?)",
            ((search_id, "pisoscom", 1), (search_id, "fotocasa", 0)),
        )

    assert enabled_portals(database_path) == ("pisoscom",)


def test_live_smoke_requires_explicit_opt_in(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SCRAPYREALESTATE_RUN_LIVE_SMOKE", raising=False)
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path.resolve()))

    with pytest.raises(SystemExit) as raised:
        main([])

    assert raised.value.code == 2


def test_enabled_portal_discovery_surfaces_invalid_database(tmp_path: Path):
    invalid = tmp_path / "invalid.sqlite3"
    invalid.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(sqlite3.DatabaseError):
        enabled_portals(invalid)
