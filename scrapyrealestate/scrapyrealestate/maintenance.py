"""Command-line maintenance operations for the persistent SQLite database."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from scrapyrealestate.persistence.backups import backup_database, restore_database
from scrapyrealestate.runtime import RuntimePaths, get_runtime_paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    paths = get_runtime_paths()
    if arguments.operation == "backup":
        destination = arguments.destination or _default_backup(paths)
        result = backup_database(paths.database_file, destination)
    else:
        result = restore_database(
            arguments.backup,
            paths.database_file,
            replace=arguments.replace,
        )
    print(result)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    backup = commands.add_parser("backup", help="create a consistent SQLite backup")
    backup.add_argument("destination", nargs="?", type=Path)
    restore = commands.add_parser("restore", help="restore a SQLite backup")
    restore.add_argument("backup", type=Path)
    restore.add_argument(
        "--replace",
        action="store_true",
        help="replace the configured database (stop the application first)",
    )
    return parser


def _default_backup(paths: RuntimePaths) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return paths.backup_dir / f"manual-{timestamp}.sqlite3"


if __name__ == "__main__":
    raise SystemExit(main())
