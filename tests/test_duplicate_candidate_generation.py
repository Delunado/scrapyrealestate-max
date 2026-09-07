from pathlib import Path

import pytest

from scrapyrealestate.persistence.database import Database
from scrapyrealestate.persistence.duplicates import DuplicateCandidateRepository
from scrapyrealestate.persistence.migrations import MIGRATIONS, MigrationRunner
from scrapyrealestate.services.duplicate_candidates import DuplicateCandidateGenerator


@pytest.fixture
def generator_context(tmp_path: Path):
    with Database(tmp_path / "generation.sqlite3").connection() as connection:
        MigrationRunner(MIGRATIONS).migrate(connection)
        repository = DuplicateCandidateRepository(connection)
        yield connection, repository


def _listing(
    connection,
    portal,
    external_id,
    *,
    location="Málaga",
    property_type="apartment",
    rooms=3,
    area=90.0,
    price=250_000,
):
    return connection.execute(
        """
        INSERT INTO listings (
            portal_key, external_id, canonical_url, transaction_type,
            property_type, title, price_euros, area_sqm, rooms, location,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, ?, ?,
                  '2026-09-07T09:00:00Z', '2026-09-07T09:00:00Z')
        RETURNING id
        """,
        (
            portal,
            external_id,
            f"https://example.com/{portal}/{external_id}",
            property_type,
            f"Piso {external_id}",
            price,
            area,
            rooms,
            location,
        ),
    ).fetchone()[0]


def test_generation_narrows_by_cross_site_location_and_structure(generator_context):
    connection, repository = generator_context
    source = _listing(connection, "pisoscom", "source")
    match = _listing(connection, "habitaclia", "match", location="málaga")
    _listing(connection, "pisoscom", "same-portal")
    _listing(connection, "fotocasa", "other-place", location="Madrid")
    _listing(connection, "yaencontre", "rooms-conflict", rooms=2)
    _listing(connection, "idealista", "price-conflict", price=400_000)

    result = DuplicateCandidateGenerator(repository).generate([source])

    assert [pair.key for pair in result.pairs] == [(source, match)]
    assert result.sources_considered == 1
    assert result.truncated_sources is False


def test_generation_requires_multiple_known_structural_attributes(generator_context):
    connection, repository = generator_context
    source = _listing(
        connection,
        "pisoscom",
        "source",
        property_type="unknown",
        rooms=None,
        area=None,
        price=None,
    )
    _listing(
        connection,
        "habitaclia",
        "sparse",
        property_type="unknown",
        rooms=None,
        area=None,
        price=None,
    )

    assert DuplicateCandidateGenerator(repository).generate([source]).pairs == ()


def test_generation_cost_is_bounded_per_source_and_across_sources(generator_context):
    connection, repository = generator_context
    sources = [
        _listing(connection, "pisoscom", f"source-{index}") for index in range(3)
    ]
    for index in range(10):
        _listing(connection, "habitaclia", f"candidate-{index}")

    result = DuplicateCandidateGenerator(
        repository, max_sources=2, max_candidates_per_source=3
    ).generate(sources)

    assert result.sources_considered == 2
    assert result.truncated_sources is True
    assert len(result.pairs) <= 6


def test_potential_match_query_uses_dedicated_narrowing_index(generator_context):
    connection, repository = generator_context
    source = _listing(connection, "pisoscom", "source")
    snapshot = repository.listing_snapshot(source)

    plan = connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT id FROM listings INDEXED BY listings_duplicate_narrowing_idx
        WHERE transaction_type = ? AND location = ? COLLATE NOCASE
        ORDER BY id DESC LIMIT 50
        """,
        (snapshot.transaction_type, snapshot.location),
    ).fetchall()

    assert any("listings_duplicate_narrowing_idx" in row["detail"] for row in plan)
