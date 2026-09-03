"""Composition root for the persistent web and scheduler application."""

from __future__ import annotations

import json
import logging
import signal
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Protocol

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
from scrapyrealestate.persistence.listings import ListingQueryRepository
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.notifications import NotificationRepository
from scrapyrealestate.persistence.prices import PriceHistoryRepository
from scrapyrealestate.persistence.runs import RunRepository
from scrapyrealestate.persistence.searches import SearchRepository
from scrapyrealestate.notifiers.registry import (
    NotifierRegistry,
    build_default_notifier_registry,
)
from scrapyrealestate.portals import build_default_registry
from scrapyrealestate.runtime import RuntimePaths, get_runtime_paths
from scrapyrealestate.services.ingestion import IngestionService
from scrapyrealestate.services.notification_delivery import DurableNotificationDispatcher
from scrapyrealestate.services.retention import OperationalRetentionService
from scrapyrealestate.services.manual_runs import ManualSearchRunLauncher
from scrapyrealestate.services.scheduler import InProcessScheduler
from scrapyrealestate.services.search_orchestration import SearchOrchestrationService
from scrapyrealestate.services.search_triggering import SearchTriggerService
from scrapyrealestate.wsgi import WaitressApplicationServer


logger = logging.getLogger(__name__)
DEFAULT_SHUTDOWN_GRACE_SECONDS = 10.0


class ApplicationServer(Protocol):
    """Blocking web-server boundary owned by the application runtime."""

    def serve(self, app: Flask) -> None: ...

    def request_shutdown(self) -> None: ...


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
        spider_runner: SpiderRunner,
        connection: sqlite3.Connection,
        report: BootstrapReport,
        manual_runs: ManualSearchRunLauncher | None = None,
    ) -> None:
        self.app = app
        self.scheduler = scheduler
        self.report = report
        self._spider_runner = spider_runner
        self._connection = connection
        self._closed = False
        self._server: ApplicationServer | None = None
        self._shutdown_requested = threading.Event()
        self._manual_runs = manual_runs

    def run(self, server: ApplicationServer) -> None:
        """Run scheduler and web server with temporary process signal handlers."""
        if self._closed:
            raise RuntimeError("application runtime is closed")
        self._server = server
        self.scheduler.start()
        try:
            with _installed_signal_handlers(self.request_shutdown):
                server.serve(self.app)
        finally:
            self.close()

    def request_shutdown(self) -> None:
        """Stop accepting new work and ask the blocking web server to return."""
        self._shutdown_requested.set()
        self.scheduler.request_stop()
        self._spider_runner.stop_accepting()
        if self._manual_runs is not None:
            self._manual_runs.stop_accepting()
        server = self._server
        if server is not None:
            server.request_shutdown()

    def close(self, grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS) -> bool:
        """Drain/terminate child crawls, stop scheduling, and close SQLite."""
        if self._closed:
            return True
        if grace_seconds < 0:
            raise ValueError("grace_seconds cannot be negative")
        self.request_shutdown()
        crawls_stopped = self._spider_runner.shutdown(grace_seconds)
        manual_runs_stopped = (
            self._manual_runs is None
            or self._manual_runs.shutdown(timeout=grace_seconds + 5.0)
        )
        scheduler_stopped = self.scheduler.stop(timeout=grace_seconds + 5.0)
        if not scheduler_stopped or not manual_runs_stopped:
            logger.error("application workers did not stop within the shutdown grace period")
            return False
        self._connection.close()
        self._closed = True
        return crawls_stopped


def build_application(
    *,
    runtime_paths: RuntimePaths | None = None,
    spider_runner: SpiderRunner | None = None,
    notifier_registry: NotifierRegistry | None = None,
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
        active_notifier_registry = notifier_registry or build_default_notifier_registry()
        retention = OperationalRetentionService(connection)
        retention.prune()
        orchestration = SearchOrchestrationService(
            registry=registry,
            runner=runner,
            runs=runs,
            ingestion=IngestionService(connection),
            runtime_paths=paths,
            notification_delivery=DurableNotificationDispatcher(
                notifications,
                active_notifier_registry,
            ),
            retention=retention.prune,
        )
        trigger = SearchTriggerService(searches, orchestration)
        manual_runs = ManualSearchRunLauncher(trigger)
        scheduler = InProcessScheduler(searches, trigger)
        app = create_app(
            runtime_paths=paths,
            database=database,
            repositories=WebRepositories(
                searches=searches,
                runs=runs,
                notifications=notifications,
                listings=ListingQueryRepository(connection),
                prices=PriceHistoryRepository(connection),
            ),
            services=WebServices(
                orchestration=orchestration,
                search_trigger=trigger,
                readiness_check=lambda: _database_ready(database),
                portals=registry,
                schedule_changed=scheduler.notify_schedule_changed,
                scheduler_running=lambda: scheduler.is_running,
                manual_runs=manual_runs,
                notifier_registry=active_notifier_registry,
            ),
        )
    except BaseException:
        connection.close()
        raise
    return ApplicationRuntime(
        app=app,
        scheduler=scheduler,
        spider_runner=runner,
        connection=connection,
        report=report,
        manual_runs=manual_runs,
    )


def main() -> None:
    """Run the transitional Flask server without waiting for ``config.json``."""
    runtime = build_application()
    runtime.run(WaitressApplicationServer())


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


@contextmanager
def _installed_signal_handlers(on_shutdown) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    signals = (signal.SIGTERM, signal.SIGINT)
    previous = {signum: signal.getsignal(signum) for signum in signals}

    def handle_signal(_signum: int, _frame: FrameType | None) -> None:
        on_shutdown()

    try:
        for signum in signals:
            signal.signal(signum, handle_signal)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
