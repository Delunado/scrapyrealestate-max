"""Transactional normalized-listing and search-match persistence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.persistence.database import transaction


class ListingMatchOutcome(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    REAPPEARED = "reappeared"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class ListingMatchResult:
    listing_id: int
    outcome: ListingMatchOutcome
    listing_created: bool


class ListingIdentityConflictError(RuntimeError):
    """A listing's external ID and canonical URL resolve to different rows."""


class ListingMatchRepository:
    """Upsert listings and their per-search visibility as one unit of work."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ingest(
        self, search_id: int, listing: NormalizedListing
    ) -> ListingMatchResult:
        observed_at = _timestamp(listing.observed_at)
        with transaction(self.connection, immediate=True):
            row = self._find_listing(listing)
            listing_created = row is None
            changed = False
            if row is None:
                listing_id = self._insert_listing(listing, observed_at)
            else:
                listing_id = row["id"]
                changed = self._is_changed(row, listing)
                self._update_listing(listing_id, listing, observed_at)

            match = self.connection.execute(
                """
                SELECT active FROM search_listing_matches
                WHERE search_id = ? AND listing_id = ?
                """,
                (search_id, listing_id),
            ).fetchone()
            if match is None:
                self.connection.execute(
                    """
                    INSERT INTO search_listing_matches (
                        search_id, listing_id, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (search_id, listing_id, observed_at, observed_at),
                )
                outcome = ListingMatchOutcome.NEW
            else:
                was_active = bool(match["active"])
                self.connection.execute(
                    """
                    UPDATE search_listing_matches
                    SET last_seen_at = ?, active = 1
                    WHERE search_id = ? AND listing_id = ?
                    """,
                    (observed_at, search_id, listing_id),
                )
                if not was_active:
                    outcome = ListingMatchOutcome.REAPPEARED
                elif changed:
                    outcome = ListingMatchOutcome.CHANGED
                else:
                    outcome = ListingMatchOutcome.UNCHANGED

        return ListingMatchResult(listing_id, outcome, listing_created)

    def get(self, listing_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()

    def _find_listing(self, listing: NormalizedListing) -> sqlite3.Row | None:
        by_external_id = None
        by_url = None
        if listing.external_id is not None:
            by_external_id = self.connection.execute(
                """
                SELECT * FROM listings WHERE portal_key = ? AND external_id = ?
                """,
                (listing.portal.value, listing.external_id),
            ).fetchone()
        if listing.canonical_url is not None:
            by_url = self.connection.execute(
                """
                SELECT * FROM listings WHERE portal_key = ? AND canonical_url = ?
                """,
                (listing.portal.value, listing.canonical_url),
            ).fetchone()
        if (
            by_external_id is not None
            and by_url is not None
            and by_external_id["id"] != by_url["id"]
        ):
            raise ListingIdentityConflictError(
                "external ID and canonical URL identify different listings"
            )
        return by_external_id or by_url

    def _insert_listing(
        self, listing: NormalizedListing, observed_at: str
    ) -> int:
        values = _listing_values(listing)
        return self.connection.execute(
            """
            INSERT INTO listings (
                portal_key, external_id, canonical_url, transaction_type,
                property_type, title, price_euros, area_sqm, rooms, bathrooms,
                floor, elevator, terrace, garage, location, neighbourhood,
                street, street_number, posted_at, first_seen_at, last_seen_at,
                raw_source_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) RETURNING id
            """,
            (*values, observed_at, observed_at, _json(dict(listing.raw_source))),
        ).fetchone()[0]

    def _update_listing(
        self, listing_id: int, listing: NormalizedListing, observed_at: str
    ) -> None:
        values = _listing_values(listing)
        self.connection.execute(
            """
            UPDATE listings SET
                portal_key = ?, external_id = ?, canonical_url = ?,
                transaction_type = ?, property_type = ?, title = ?,
                price_euros = ?, area_sqm = ?, rooms = ?, bathrooms = ?, floor = ?,
                elevator = ?, terrace = ?, garage = ?, location = ?,
                neighbourhood = ?, street = ?, street_number = ?, posted_at = ?,
                last_seen_at = ?, active = 1, raw_source_json = ?
            WHERE id = ?
            """,
            (*values, observed_at, _json(dict(listing.raw_source)), listing_id),
        )

    @staticmethod
    def _is_changed(row: sqlite3.Row, listing: NormalizedListing) -> bool:
        persisted = tuple(row[name] for name in _MATERIAL_COLUMNS)
        incoming = _listing_values(listing)
        return persisted != incoming


_MATERIAL_COLUMNS = (
    "portal_key",
    "external_id",
    "canonical_url",
    "transaction_type",
    "property_type",
    "title",
    "price_euros",
    "area_sqm",
    "rooms",
    "bathrooms",
    "floor",
    "elevator",
    "terrace",
    "garage",
    "location",
    "neighbourhood",
    "street",
    "street_number",
    "posted_at",
)


def _listing_values(listing: NormalizedListing) -> tuple[Any, ...]:
    return (
        listing.portal.value,
        listing.external_id,
        listing.canonical_url,
        listing.transaction_type.value,
        listing.property_type.value,
        listing.title,
        listing.price_euros,
        listing.area_sqm,
        listing.rooms,
        listing.bathrooms,
        listing.floor,
        listing.elevator.value,
        listing.terrace.value,
        listing.garage.value,
        listing.location,
        listing.neighbourhood,
        listing.street,
        listing.street_number,
        _timestamp(listing.posted_at) if listing.posted_at is not None else None,
    )


def _timestamp(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
