import sqlite3
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


@pytest.fixture
def migrated_connection(tmp_path: Path):
    database = Database(tmp_path / "application.sqlite3")
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        yield connection


def test_search_schema_is_created_at_version_one(migrated_connection):
    connection = migrated_connection
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert connection.execute(
        "SELECT max(version) FROM schema_migrations"
    ).fetchone()[0] == len(MIGRATIONS)

    search_id = connection.execute(
        """
        INSERT INTO searches (name, transaction_type, filters_json)
        VALUES ('Centro', 'buy', '{"min_price_euros": 100000}')
        RETURNING id
        """
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (?, 600)",
        (search_id,),
    )

    search = connection.execute("SELECT * FROM searches").fetchone()
    assert search["enabled"] == 1
    assert search["version"] == 1
    assert search["created_at"].endswith("Z")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO searches (name, transaction_type) VALUES ('', 'buy')",
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'lease')",
        "INSERT INTO searches (name, transaction_type, filters_json) "
        "VALUES ('A', 'buy', '[]')",
    ],
)
def test_search_constraints_reject_invalid_records(migrated_connection, statement):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(statement)


def test_schedule_requires_a_search_and_minimum_interval(migrated_connection):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (999, 600)"
        )

    search_id = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'rent') RETURNING id"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            "INSERT INTO search_schedules (search_id, interval_seconds) VALUES (?, 299)",
            (search_id,),
        )


def test_search_portal_schema_stores_validated_selection(migrated_connection):
    search_id = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
    ).fetchone()[0]
    migrated_connection.execute(
        """
        INSERT INTO search_portals (
            search_id, portal_key, raw_url_override, adapter_options_json, enabled
        ) VALUES (?, 'pisoscom', 'https://www.pisos.com/venta/pisos-madrid/',
                  '{"recent_sort": true}', 0)
        """,
        (search_id,),
    )

    selection = migrated_connection.execute("SELECT * FROM search_portals").fetchone()
    assert selection["portal_key"] == "pisoscom"
    assert selection["enabled"] == 0
    assert selection["created_at"].endswith("Z")


@pytest.mark.parametrize(
    ("portal_key", "raw_url", "options"),
    [
        ("Pisos", None, "{}"),
        ("pisos.com", None, "{}"),
        ("pisoscom", "ftp://example.com/results", "{}"),
        ("pisoscom", None, "[]"),
    ],
)
def test_search_portal_constraints_reject_invalid_values(
    migrated_connection, portal_key, raw_url, options
):
    search_id = migrated_connection.execute(
        "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
    ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        migrated_connection.execute(
            """
            INSERT INTO search_portals (
                search_id, portal_key, raw_url_override, adapter_options_json
            ) VALUES (?, ?, ?, ?)
            """,
            (search_id, portal_key, raw_url, options),
        )
