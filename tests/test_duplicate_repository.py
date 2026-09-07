import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.duplicates import (
    DuplicateCandidateRepository,
    DuplicateReviewState,
)
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner


NOW = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)


@pytest.fixture
def candidates(tmp_path: Path):
    with Database(tmp_path / "duplicates.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        first = _insert_listing(connection, "pisoscom", "1", "Piso centro")
        second = _insert_listing(connection, "habitaclia", "2", "Piso en el centro")
        same_portal = _insert_listing(connection, "pisoscom", "3", "Otro piso")
        yield DuplicateCandidateRepository(connection), connection, first, second, same_portal


def _insert_listing(connection, portal, external_id, title):
    return connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, canonical_url, transaction_type, title,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, 'buy', ?, '2026-09-07T09:00:00Z',
                  '2026-09-07T09:00:00Z')
        RETURNING id
        """,
        (portal, external_id, f"https://example.com/{portal}/{external_id}", title),
    ).fetchone()[0]


def test_upsert_pair_persists_score_reasons_and_members(candidates):
    repository, _connection, first, second, _same_portal = candidates

    created = repository.upsert_pair(
        second,
        first,
        score=0.91,
        reasons=[{"code": "same_address", "detail": "calle mayor 4"}],
        observed_at=NOW,
    )

    assert created.pair_key == f"{first}:{second}"
    assert created.score == pytest.approx(0.91)
    assert created.review_state is DuplicateReviewState.PENDING
    assert [member.listing_id for member in created.members] == [first, second]
    assert created.reasons[0]["code"] == "same_address"


def test_upsert_updates_evidence_without_duplicating_membership_history(candidates):
    repository, connection, first, second, _same_portal = candidates
    initial = repository.upsert_pair(first, second, score=0.9, reasons=[{"code": "first"}])

    updated = repository.upsert_pair(
        first, second, score=0.95, reasons=[{"code": "rescored"}]
    )

    assert updated.id == initial.id
    assert updated.score == pytest.approx(0.95)
    assert updated.reasons == ({"code": "rescored"},)
    assert connection.execute(
        "SELECT count(*) FROM duplicate_candidate_memberships WHERE group_id = ?",
        (initial.id,),
    ).fetchone()[0] == 2


def test_review_state_and_rejection_are_durable(candidates):
    repository, _connection, first, second, _same_portal = candidates
    group = repository.upsert_pair(first, second, score=0.92, reasons=[{"code": "match"}])

    rejected = repository.set_review_state(
        group.id, DuplicateReviewState.REJECTED, reviewed_at=NOW
    )

    assert rejected.review_state is DuplicateReviewState.REJECTED
    assert rejected.reviewed_at == "2026-09-07T10:00:00Z"
    assert repository.is_rejected_pair(second, first) is True
    assert repository.list(review_state=DuplicateReviewState.REJECTED) == (rejected,)


def test_candidate_pair_requires_existing_cross_site_listings(candidates):
    repository, _connection, first, second, same_portal = candidates

    with pytest.raises(ValueError, match="different portals"):
        repository.upsert_pair(first, same_portal, score=0.9, reasons=[{"code": "bad"}])
    with pytest.raises(LookupError, match="must exist"):
        repository.upsert_pair(first, 999, score=0.9, reasons=[{"code": "bad"}])
    with pytest.raises(ValueError, match="different listings"):
        repository.upsert_pair(second, second, score=0.9, reasons=[{"code": "bad"}])


def test_schema_rejects_invalid_candidate_state_and_evidence(candidates):
    _repository, connection, _first, _second, _same_portal = candidates

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO duplicate_candidate_groups (pair_key, score, reasons_json)
            VALUES ('1:2', 1.2, '[]')
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO duplicate_candidate_groups (
                pair_key, score, reasons_json, review_state
            ) VALUES ('1:3', 0.9, '{}', 'pending')
            """
        )
