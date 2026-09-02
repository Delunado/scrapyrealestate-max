"""Durable, secret-free reports for legacy migration operations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LegacyImportReport:
    import_key: str
    source_name: str
    source_digest: str
    rollback_source_marker: str
    source_preserved: bool
    imported_records: int
    ignored_records: int
    warnings: tuple[str, ...]
    completed_at: str


class LegacyImportReportRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record(
        self,
        *,
        import_key: str,
        source_name: str,
        source_digest: str,
        imported_records: int,
        ignored_records: int,
        warnings: list[str] | tuple[str, ...],
        completed_at: str,
    ) -> LegacyImportReport:
        marker = f"preserved-source-sha256:{source_digest}"
        self.connection.execute(
            """
            INSERT INTO legacy_import_reports (
                import_key, source_name, source_digest, rollback_source_marker,
                source_preserved, imported_records, ignored_records,
                warnings_json, completed_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT (import_key) DO NOTHING
            """,
            (
                import_key,
                source_name,
                source_digest,
                marker,
                imported_records,
                ignored_records,
                json.dumps(tuple(warnings), ensure_ascii=False),
                completed_at,
            ),
        )
        return self.get(import_key)

    def get(self, import_key: str) -> LegacyImportReport:
        row = self.connection.execute(
            "SELECT * FROM legacy_import_reports WHERE import_key = ?", (import_key,)
        ).fetchone()
        if row is None:
            raise LookupError(f"legacy import report {import_key!r} does not exist")
        return _report(row)

    def list(self) -> tuple[LegacyImportReport, ...]:
        rows = self.connection.execute(
            "SELECT * FROM legacy_import_reports ORDER BY completed_at, import_key"
        ).fetchall()
        return tuple(_report(row) for row in rows)


def _report(row: sqlite3.Row) -> LegacyImportReport:
    return LegacyImportReport(
        import_key=row["import_key"],
        source_name=row["source_name"],
        source_digest=row["source_digest"],
        rollback_source_marker=row["rollback_source_marker"],
        source_preserved=bool(row["source_preserved"]),
        imported_records=row["imported_records"],
        ignored_records=row["ignored_records"],
        warnings=tuple(json.loads(row["warnings_json"])),
        completed_at=row["completed_at"],
    )
