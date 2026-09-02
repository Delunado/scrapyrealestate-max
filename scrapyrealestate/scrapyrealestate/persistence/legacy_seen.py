"""Conservative import and lookup of portal-unscoped legacy listing IDs.

Legacy ``ids.json`` contains only integer IDs, so these records must never be
assigned to a portal or used to fabricate normalized listings. A match suppresses
an initial notification across portals, favouring no repeat flood over false novelty.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scrapyrealestate.persistence.database import transaction


IDS_IMPORT_MARKER = "legacy.ids_import.v1"


class LegacyIdsImportError(ValueError):
    """The legacy ID source cannot be safely interpreted."""


@dataclass(frozen=True, slots=True)
class LegacyIdsImportResult:
    imported: bool
    imported_count: int
    duplicate_count: int
    ignored_count: int
    source_digest: str
    warnings: tuple[str, ...]


class LegacySeenRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def import_file(self, ids_file: Path) -> LegacyIdsImportResult:
        source = Path(ids_file)
        raw_bytes = source.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        marker = self._marker()
        if marker is not None:
            return LegacyIdsImportResult(
                imported=False,
                imported_count=marker["imported_count"],
                duplicate_count=marker["duplicate_count"],
                ignored_count=marker["ignored_count"],
                source_digest=marker["source_digest"],
                warnings=tuple(marker["warnings"]),
            )
        try:
            values = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise LegacyIdsImportError("ids.json must contain valid UTF-8 JSON") from error
        if not isinstance(values, list):
            raise LegacyIdsImportError("ids.json top-level value must be a list")

        normalized: list[int] = []
        ignored_count = 0
        for value in values:
            legacy_id = _legacy_id(value)
            if legacy_id is None:
                ignored_count += 1
            else:
                normalized.append(legacy_id)
        unique_ids = tuple(dict.fromkeys(normalized))
        duplicate_count = len(normalized) - len(unique_ids)
        warnings = [
            "legacy IDs have no portal scope and suppress first notifications globally"
        ]
        if ignored_count:
            warnings.append(f"ignored {ignored_count} invalid legacy ID values")
        timestamp = _utc_now()
        with transaction(self.connection, immediate=True):
            before = self.connection.total_changes
            self.connection.executemany(
                """
                INSERT INTO legacy_seen_ids (legacy_id, imported_at)
                VALUES (?, ?) ON CONFLICT DO NOTHING
                """,
                ((legacy_id, timestamp) for legacy_id in unique_ids),
            )
            imported_count = self.connection.total_changes - before
            marker_value = {
                "imported_count": imported_count,
                "duplicate_count": duplicate_count,
                "ignored_count": ignored_count,
                "source_digest": digest,
                "source_name": source.name,
                "warnings": warnings,
                "imported_at": timestamp,
            }
            self.connection.execute(
                """
                INSERT INTO application_settings (
                    key, value_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    IDS_IMPORT_MARKER,
                    json.dumps(marker_value, sort_keys=True, separators=(",", ":")),
                    timestamp,
                    timestamp,
                ),
            )
        return LegacyIdsImportResult(
            imported=True,
            imported_count=imported_count,
            duplicate_count=duplicate_count,
            ignored_count=ignored_count,
            source_digest=digest,
            warnings=tuple(warnings),
        )

    def was_seen(self, external_id: str | int | None) -> bool:
        """Return a conservative global match; portal scope is unknowable."""
        legacy_id = _legacy_id(external_id)
        if legacy_id is None:
            return False
        return (
            self.connection.execute(
                "SELECT 1 FROM legacy_seen_ids WHERE legacy_id = ?", (legacy_id,)
            ).fetchone()
            is not None
        )

    def count(self) -> int:
        return self.connection.execute("SELECT count(*) FROM legacy_seen_ids").fetchone()[
            0
        ]

    def _marker(self) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT value_json FROM application_settings WHERE key = ?",
            (IDS_IMPORT_MARKER,),
        ).fetchone()
        return json.loads(row["value_json"]) if row is not None else None


def _legacy_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
