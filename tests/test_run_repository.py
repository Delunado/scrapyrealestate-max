from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scrapyrealestate.domain.values import PortalKey, RunStatus
from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.persistence.runs import (
    RunCounts,
    RunRepository,
    SearchRunStatus,
    TriggerKind,
)


@pytest.fixture
def repository(tmp_path: Path):
    with Database(tmp_path / "test.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        search_id = connection.execute(
            "INSERT INTO searches (name, transaction_type) VALUES ('A', 'buy') RETURNING id"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO search_schedules (search_id, interval_seconds, next_run_at)
            VALUES (?, 600, '2026-09-02T12:00:00Z')
            """,
            (search_id,),
        )
        yield RunRepository(connection), search_id


def _time(minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, 10, minute, tzinfo=timezone.utc)


def test_run_and_attempt_lifecycle_and_latest_status(repository):
    runs, search_id = repository
    run = runs.create_run(search_id, TriggerKind.SCHEDULED, scheduled_for=_time())
    run = runs.start_run(run.id, _time())
    attempt = runs.start_attempt(run.id, PortalKey.PISOSCOM, _time())
    attempt = runs.finish_attempt(
        attempt.id,
        RunStatus.SUCCESS,
        _time(1),
        counts=RunCounts(returned=10, matched=8, new=3, changed=1),
    )
    run = runs.finish_run(
        run.id,
        SearchRunStatus.SUCCESS,
        _time(2),
        counts=attempt.counts,
    )

    latest = runs.latest_status(search_id)
    assert latest.run == run
    assert latest.run.duration_seconds == 120
    assert latest.run.counts.new == 3
    assert latest.attempts == (attempt,)
    assert latest.attempts[0].duration_seconds == 60
    assert latest.next_run_at == "2026-09-02T12:00:00Z"


def test_failed_attempt_exposes_bounded_redacted_error(repository):
    runs, search_id = repository
    run = runs.start_run(
        runs.create_run(search_id, TriggerKind.MANUAL).id, _time()
    )
    attempt = runs.start_attempt(run.id, PortalKey.IDEALISTA, _time())
    failed = runs.finish_attempt(
        attempt.id,
        RunStatus.BLOCKED,
        _time(1),
        error_category="challenge",
        redacted_diagnostic="x" * 2100,
    )

    assert failed.error_category == "challenge"
    assert len(failed.redacted_diagnostic) == 2000


def test_invalid_lifecycle_transitions_are_rejected(repository):
    runs, search_id = repository
    pending = runs.create_run(search_id, TriggerKind.MANUAL)
    with pytest.raises(LookupError):
        runs.finish_run(pending.id, SearchRunStatus.FAILED, _time())

    running = runs.start_run(pending.id, _time())
    with pytest.raises(LookupError):
        runs.start_run(running.id, _time())
    with pytest.raises(ValueError):
        runs.finish_run(running.id, SearchRunStatus.RUNNING, _time(1))


def test_timestamps_are_normalized_to_utc(repository):
    runs, search_id = repository
    madrid_time = _time().astimezone(timezone(timedelta(hours=2)))

    run = runs.create_run(search_id, TriggerKind.SCHEDULED, scheduled_for=madrid_time)

    assert run.scheduled_for == "2026-09-02T10:00:00Z"
