import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scrapyrealestate.bootstrap import build_application
from scrapyrealestate.domain.notification import NotificationEventType
from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.execution.contract import PortalRunRequest
from scrapyrealestate.execution.runner import SpiderRunner
from scrapyrealestate.notifiers import DeliveryResult, NotifierRegistry
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.migrations.runner import Migration
from scrapyrealestate.persistence.notifications import (
    DeliveryStatus,
    NotificationProvider,
    NotificationRepository,
)
from scrapyrealestate.runtime import RuntimePaths
from scrapyrealestate.services.notification_delivery import (
    DeliveryPolicy,
    DurableNotificationDispatcher,
)


FAKE_SPIDER = Path(__file__).parent / "fixtures" / "execution" / "fake_spider.py"
UTC = timezone.utc


def test_corrupt_legacy_sources_are_quarantined_across_restart(tmp_path: Path):
    paths = RuntimePaths((tmp_path / "data").resolve())
    paths.ensure_data_dir()
    paths.config_file.write_bytes(b"\xff\xfebroken-json")
    paths.ids_file.write_text('{"ids": [1]}', encoding="utf-8")

    first = build_application(runtime_paths=paths)
    try:
        assert first.report.import_warnings == (
            "legacy configuration import was skipped",
            "legacy ID import was skipped",
        )
        assert first.app.test_client().get("/readyz").status_code == 200
    finally:
        assert first.close(grace_seconds=1)

    restarted = build_application(runtime_paths=paths)
    try:
        assert restarted.report.schema_version == len(MIGRATIONS)
        assert restarted.app.test_client().get("/readyz").status_code == 200
        assert paths.config_file.read_bytes() == b"\xff\xfebroken-json"
    finally:
        assert restarted.close(grace_seconds=1)


def test_failed_migration_can_be_retried_without_partial_schema(tmp_path: Path):
    database = Database(tmp_path / "migration.sqlite3")
    should_fail = True

    def injected_failure(connection):
        connection.execute("CREATE TABLE recovered (id INTEGER PRIMARY KEY) STRICT")
        if should_fail:
            raise RuntimeError("injected migration failure")

    runner = MigrationRunner((Migration(1, "recoverable", injected_failure),))
    with database.connection() as connection:
        with pytest.raises(RuntimeError, match="injected migration failure"):
            runner.migrate(connection)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'recovered'"
        ).fetchone() is None
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0

        should_fail = False
        assert runner.migrate(connection) == 1
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'recovered'"
        ).fetchone() is not None


def test_locked_sqlite_write_fails_boundedly_then_recovers(tmp_path: Path):
    database = Database(tmp_path / "locked.sqlite3", busy_timeout_ms=25)
    with database.connection() as owner, database.connection() as contender:
        MigrationRunner(MIGRATIONS).migrate(owner)
        owner.execute("BEGIN IMMEDIATE")
        owner.execute("INSERT INTO searches (name, transaction_type) VALUES ('owner', 'buy')")

        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            contender.execute(
                "INSERT INTO searches (name, transaction_type) VALUES ('contender', 'buy')"
            )
        assert time.monotonic() - started < 1

        owner.rollback()
        contender.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('recovered', 'buy')"
        )
        assert contender.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert contender.execute("SELECT name FROM searches").fetchall()[0][0] == "recovered"


def _request(tmp_path: Path, output_name: str = "attempt.jl") -> PortalRunRequest:
    return PortalRunRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url="https://www.pisos.com/venta/pisos-madrid/",
        transaction_type=TransactionType.BUY,
        output_path=tmp_path / output_name,
        timeout_seconds=30,
    )


def _runner(tmp_path: Path, mode: str) -> SpiderRunner:
    return SpiderRunner(
        working_directory=tmp_path,
        build_command=lambda request: [
            sys.executable,
            str(FAKE_SPIDER),
            mode,
            str(request.output_path),
        ],
    )


def test_killed_spider_and_malformed_output_remain_isolated(tmp_path: Path):
    hanging = _runner(tmp_path, "hang")
    results = []
    worker = threading.Thread(target=lambda: results.append(hanging.run(_request(tmp_path))))
    worker.start()
    deadline = time.monotonic() + 2
    while hanging.active_process_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert hanging.active_process_count == 1
    assert hanging.shutdown(grace_seconds=0.05)
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert results[0].status is RunStatus.TRANSPORT_ERROR
    assert not (tmp_path / "attempt.jl").exists()

    malformed = _runner(tmp_path, "malformed").run(
        _request(tmp_path, "malformed.jl")
    )
    assert malformed.status is RunStatus.PARSER_ERROR
    assert malformed.items == ()
    assert "not valid JSON" in malformed.diagnostic


class _ResultNotifier:
    def __init__(self, result: DeliveryResult) -> None:
        self.result = result

    def send(self, _event):
        return self.result


def test_notifier_timeout_retry_is_recovered_after_restart(tmp_path: Path):
    database = Database(tmp_path / "delivery.sqlite3")
    now = datetime(2026, 9, 7, 12, tzinfo=UTC)
    with database.connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('Recovery', 'buy') RETURNING id"
        ).fetchone()[0]
        repository = NotificationRepository(connection)
        channel = repository.create_channel(
            "Timeout webhook",
            NotificationProvider.WEBHOOK,
            config={"endpoint_url": "https://example.invalid/hook"},
            secret_config={"authorization": "restart-secret"},
        )
        repository.assign_channel(search_id, channel.id)
        event = repository.create_event(
            search_id,
            NotificationEventType.NEW_LISTING,
            "restart-event",
            now,
            payload={"title": "Fixture home"},
        ).event
        failing = NotifierRegistry()
        failing.register(
            NotificationProvider.WEBHOOK,
            lambda _channel: _ResultNotifier(
                DeliveryResult.failed("timeout", "webhook request timed out")
            ),
        )
        dispatcher = DurableNotificationDispatcher(
            repository,
            failing,
            policy=DeliveryPolicy(base_backoff_seconds=5, max_backoff_seconds=5),
            clock=lambda: now,
        )
        dispatcher.enqueue(event.id)
        failed = dispatcher.dispatch_next()
        assert failed.completion.attempt.status is DeliveryStatus.FAILED
        assert failed.completion.retry.status is DeliveryStatus.PENDING

    with database.connection() as restarted_connection:
        repository = NotificationRepository(restarted_connection)
        succeeding = NotifierRegistry()
        succeeding.register(
            NotificationProvider.WEBHOOK,
            lambda _channel: _ResultNotifier(DeliveryResult.delivered("recovered")),
        )
        restarted = DurableNotificationDispatcher(
            repository,
            succeeding,
            clock=lambda: now + timedelta(seconds=5),
        )
        outcome = restarted.dispatch_next()

        assert outcome.completion.attempt.status is DeliveryStatus.SUCCEEDED
        assert outcome.completion.attempt.provider_message_id == "recovered"
        assert restarted.dispatch_next() is None
        rows = restarted_connection.execute(
            "SELECT status, error_category, redacted_diagnostic FROM notification_delivery_attempts ORDER BY attempt_number"
        ).fetchall()
        assert [row["status"] for row in rows] == ["failed", "succeeded"]
        assert rows[0]["error_category"] == "timeout"
        assert "restart-secret" not in str([dict(row) for row in rows])
