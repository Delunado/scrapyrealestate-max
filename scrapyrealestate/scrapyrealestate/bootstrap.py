"""Composition root for the persistent web and scheduler application."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from scrapyrealestate.execution.runner import SpiderRunner
from scrapyrealestate.flask_server import (
    WebRepositories,
    WebServices,
    create_app,
)
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.legacy_import import LegacyConfigImporter
from scrapyrealestate.persistence.legacy_seen import LegacySeenRepository
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import NotificationRepository
from scrapyrealestate.persistence.runs import RunRepository
from scrapyrealestate.persistence.searches import SearchRepository
from scrapyrealestate.portals import build_default_registry
from scrapyrealestate.runtime import RuntimePaths, get_runtime_paths
from scrapyrealestate.services.ingestion import IngestionService
from scrapyrealestate.services.scheduler import InProcessScheduler
from scrapyrealestate.services.search_orchestration import SearchOrchestrationService
from scrapyrealestate.services.search_triggering import SearchTriggerService


logger = logging.getLogger(__name__)
ServeApplication = Callable[[Flask], None]


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    schema_version: int
    config_imported: bool = False
    ids_imported: bool = False
    import_warnings: tuple[str, ...] = ()


class ApplicationRuntime:
    """Own the resources created by one persistent application bootstrap."""

    def __init__(
        self,
        *,
        app: Flask,
        scheduler: InProcessScheduler,
        connection: sqlite3.Connection,
        report: BootstrapReport,
    ) -> None:
        self.app = app
        self.scheduler = scheduler
        self.report = report
        self._connection = connection
        self._closed = False

    def run(self, serve: ServeApplication) -> None:
        """Run the scheduler beside a blocking web server callable."""
        if self._closed:
            raise RuntimeError("application runtime is closed")
        self.scheduler.start()
        try:
            serve(self.app)
        finally:
            self.close()

    def close(self) -> None:
        """Stop scheduling and close the application-owned database connection."""
        if self._closed:
            return
        self.scheduler.stop()
        self._connection.close()
        self._closed = True


def build_application(
    *,
    runtime_paths: RuntimePaths | None = None,
    spider_runner: SpiderRunner | None = None,
) -> ApplicationRuntime:
    """Migrate/import once and compose the persistent web/scheduler runtime."""
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_data_dir()
    database = Database(paths.database_file)
    # This dedicated connection is used by the scheduler worker after bootstrap.
    # Web readiness opens a short independent connection instead of sharing it.
    connection = database.connect(check_same_thread=False)
    try:
        schema_version = MigrationRunner(MIGRATIONS).migrate(connection)
        report = _import_legacy_sources(connection, paths, schema_version)
        searches = SearchRepository(connection)
        runs = RunRepository(connection)
        notifications = NotificationRepository(connection)
        runner = spider_runner or SpiderRunner(
            working_directory=Path(__file__).resolve().parents[1]
        )
        registry = build_default_registry(
            idealista_proxy=_uses_idealista_proxy(connection)
        )
        orchestration = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=IngestionService(connection),
            runtime_paths=paths,
        )
        trigger = SearchTriggerService(searches, orchestration)
        scheduler = InProcessScheduler(searches, trigger)
        app = create_app(
            runtime_paths=paths,
            repositories=WebRepositories(
                searches=searches,
                runs=runs,
                notifications=notifications,
            ),
            services=WebServices(
                orchestration=orchestration,
                search_trigger=trigger,
                readiness_check=lambda: _database_ready(database),
            ),
        )
    except BaseException:
        connection.close()
        raise
    return ApplicationRuntime(
        app=app,
        scheduler=scheduler,
        connection=connection,
        report=report,
    )


def main() -> None:
    """Run the transitional Flask server without waiting for ``config.json``."""
    runtime = build_application()
    runtime.run(
        lambda app: app.run(
            host="0.0.0.0",
            port=8080,
            use_reloader=False,
            threaded=False,
        )
    )


def _import_legacy_sources(
    connection: sqlite3.Connection,
    paths: RuntimePaths,
    schema_version: int,
) -> BootstrapReport:
    config_imported = False
    ids_imported = False
    warnings: list[str] = []
    if paths.config_file.is_file():
        try:
            result = LegacyConfigImporter(connection).import_file(paths.config_file)
            config_imported = result.imported
            warnings.extend(result.warnings)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("legacy configuration import was skipped")
            warnings.append("legacy configuration import was skipped")
    if paths.ids_file.is_file():
        try:
            result = LegacySeenRepository(connection).import_file(paths.ids_file)
            ids_imported = result.imported
            warnings.extend(result.warnings)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("legacy ID import was skipped")
            warnings.append("legacy ID import was skipped")
    return BootstrapReport(
        schema_version=schema_version,
        config_imported=config_imported,
        ids_imported=ids_imported,
        import_warnings=tuple(warnings),
    )


def _uses_idealista_proxy(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM search_portals WHERE portal_key = 'idealista_proxy' LIMIT 1"
        ).fetchone()
        is not None
    )


def _database_ready(database: Database) -> bool:
    try:
        with database.connection() as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return False
    return True


if __name__ == "__main__":
    main()
