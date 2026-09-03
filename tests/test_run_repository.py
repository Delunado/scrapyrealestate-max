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


def test_portal_health_counts_recent_status_categories(repository):
    runs, search_id = repository
    run = runs.start_run(
        runs.create_run(search_id, TriggerKind.MANUAL).id, _time()
    )
    statuses = (
        RunStatus.EMPTY,
        RunStatus.BLOCKED,
        RunStatus.PARSER_ERROR,
        RunStatus.UNAVAILABLE,
    )
    for number, status in enumerate(statuses, start=1):
        attempt = runs.start_attempt(
            run.id,
            PortalKey.PISOSCOM,
            _time(number),
            attempt_number=number,
        )
        runs.finish_attempt(attempt.id, status, _time(number + 1))

    health = runs.portal_health()[0]

    assert health.portal is PortalKey.PISOSCOM
    assert health.sample_size == 4
    assert health.latest_status == RunStatus.UNAVAILABLE.value
    assert health.empty_count == 1
    assert health.blocked_count == 1
    assert health.parser_error_count == 1
    assert health.unavailable_count == 1
    assert health.conclusive_count == 1


def test_portal_health_validates_sample_bound(repository):
    runs, _search_id = repository

    with pytest.raises(ValueError):
        runs.portal_health(attempts_per_portal=101)


def test_portal_health_uses_only_the_bounded_newest_sample(repository):
    runs, search_id = repository
    run = runs.start_run(
        runs.create_run(search_id, TriggerKind.MANUAL).id, _time()
    )
    for number, status in enumerate(
        (RunStatus.SUCCESS, RunStatus.EMPTY, RunStatus.BLOCKED), start=1
    ):
        attempt = runs.start_attempt(
            run.id, PortalKey.FOTOCASA, _time(number), attempt_number=number
        )
        runs.finish_attempt(attempt.id, status, _time(number + 1))

    health = runs.portal_health(attempts_per_portal=2)[0]

    assert health.sample_size == 2
    assert health.latest_status == RunStatus.BLOCKED.value
    assert health.success_count == 0
    assert health.empty_count == 1
    assert health.blocked_count == 1
