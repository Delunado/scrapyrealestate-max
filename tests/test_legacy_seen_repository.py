import json
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.legacy_seen import (
    LegacyIdsImportError,
    LegacySeenRepository,
)
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


def test_imports_ids_without_fabricating_portal_listings(tmp_path: Path):
    ids_file = tmp_path / "ids.json"
    ids_file.write_text(json.dumps([123, "456", 123, -1, "bad"]), encoding="utf-8")
    original = ids_file.read_bytes()
    with Database(tmp_path / "app.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        seen = LegacySeenRepository(connection)

        result = seen.import_file(ids_file)

        assert result.imported_count == 2
        assert result.duplicate_count == 1
        assert result.ignored_count == 2
        assert seen.was_seen("123") is True
        assert seen.was_seen(456) is True
        assert seen.was_seen("portal-id") is False
        assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 0
        assert ids_file.read_bytes() == original
        assert any("no portal scope" in warning for warning in result.warnings)


def test_ids_import_rerun_is_idempotent(tmp_path: Path):
    ids_file = tmp_path / "ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    with Database(tmp_path / "app.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        seen = LegacySeenRepository(connection)

        first = seen.import_file(ids_file)
        repeated = seen.import_file(ids_file)

        assert first.imported is True
        assert repeated.imported is False
        assert seen.count() == 3


@pytest.mark.parametrize("content", ["{}", "not JSON", '"text"'])
def test_invalid_ids_sources_are_rejected_without_partial_import(
    tmp_path: Path, content: str
):
    ids_file = tmp_path / "ids.json"
    ids_file.write_text(content, encoding="utf-8")
    with Database(tmp_path / "app.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        seen = LegacySeenRepository(connection)

        with pytest.raises(LegacyIdsImportError):
            seen.import_file(ids_file)

        assert seen.count() == 0
