"""Persistence for reviewable, non-destructive duplicate candidate groups."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from scrapyrealestate.domain.values import PortalKey
from scrapyrealestate.persistence.database import transaction


class DuplicateReviewState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DuplicateCandidateMember:
    listing_id: int
    portal: PortalKey
    title: str
    canonical_url: str | None
    added_at: str
    removed_at: str | None


@dataclass(frozen=True, slots=True)
class DuplicateListingSnapshot:
    id: int
    portal: PortalKey
    transaction_type: str
    property_type: str
    title: str
    price_euros: int | None
    area_sqm: float | None
    rooms: int | None
    location: str | None
    neighbourhood: str | None
    street: str | None
    street_number: str | None


@dataclass(frozen=True, slots=True)
class DuplicateCandidateGroup:
    id: int
    pair_key: str
    score: float
    reasons: tuple[dict[str, Any], ...]
    review_state: DuplicateReviewState
    created_at: str
    updated_at: str
    reviewed_at: str | None
    members: tuple[DuplicateCandidateMember, ...]


class DuplicateCandidateRepository:
    """Store scored cross-site pairs without changing normalized listing identity."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert_pair(
        self,
        first_listing_id: int,
        second_listing_id: int,
        *,
        score: float,
        reasons: tuple[dict[str, Any], ...] | list[dict[str, Any]],
        observed_at: datetime | None = None,
    ) -> DuplicateCandidateGroup:
        with transaction(self.connection, immediate=True):
            group_id = self.upsert_pair_locked(
                first_listing_id,
                second_listing_id,
                score=score,
                reasons=reasons,
                observed_at=observed_at,
            )
        return self.get(group_id)

    def listing_snapshot(self, listing_id: int) -> DuplicateListingSnapshot:
        row = self.connection.execute(
            """
            SELECT id, portal_key, transaction_type, property_type, title,
                   price_euros, area_sqm, rooms, location, neighbourhood,
                   street, street_number
            FROM listings WHERE id = ?
            """,
            (listing_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"listing {listing_id} does not exist")
        return _snapshot(row)

    def potential_matches(
        self,
        listing_id: int,
        *,
        limit: int = 50,
        price_window: float = 0.15,
        area_window: float = 0.15,
    ) -> tuple[DuplicateListingSnapshot, ...]:
        """Return an indexed, bounded pool before domain-level candidate checks."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        for name, window in (("price_window", price_window), ("area_window", area_window)):
            if not 0 <= window <= 0.5:
                raise ValueError(f"{name} must be between 0 and 0.5")
        source = self.listing_snapshot(listing_id)
        if not source.location:
            return ()
        rows = self.connection.execute(
            """
            SELECT id, portal_key, transaction_type, property_type, title,
                   price_euros, area_sqm, rooms, location, neighbourhood,
                   street, street_number
            FROM listings INDEXED BY listings_duplicate_narrowing_idx
            WHERE transaction_type = ?
              AND location = ? COLLATE NOCASE
              AND portal_key != ?
              AND id != ?
              AND (? IS NULL OR rooms IS NULL OR rooms = ?)
              AND (
                    ? = 'unknown' OR property_type = 'unknown'
                    OR property_type = ?
              )
              AND (
                    ? IS NULL OR price_euros IS NULL
                    OR price_euros BETWEEN ? AND ?
              )
              AND (
                    ? IS NULL OR area_sqm IS NULL
                    OR area_sqm BETWEEN ? AND ?
              )
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                source.transaction_type,
                source.location,
                source.portal.value,
                source.id,
                source.rooms,
                source.rooms,
                source.property_type,
                source.property_type,
                source.price_euros,
                None
                if source.price_euros is None
                else source.price_euros * (1 - price_window),
                None
                if source.price_euros is None
                else source.price_euros * (1 + price_window),
                source.area_sqm,
                None if source.area_sqm is None else source.area_sqm * (1 - area_window),
                None if source.area_sqm is None else source.area_sqm * (1 + area_window),
                limit,
            ),
        ).fetchall()
        return tuple(_snapshot(row) for row in rows)

    def upsert_pair_locked(
        self,
        first_listing_id: int,
        second_listing_id: int,
        *,
        score: float,
        reasons: tuple[dict[str, Any], ...] | list[dict[str, Any]],
        observed_at: datetime | None = None,
    ) -> int:
        """Upsert inside a caller-owned transaction and return the group ID."""
        first_id, second_id = _validate_pair(first_listing_id, second_listing_id)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise TypeError("score must be numeric")
        if not 0.0 <= float(score) <= 1.0:
            raise ValueError("score must be between 0 and 1")
        serialized_reasons = _serialize_reasons(reasons)
        timestamp = _utc_timestamp(observed_at)
        rows = self.connection.execute(
            "SELECT id, portal_key FROM listings WHERE id IN (?, ?) ORDER BY id",
            (first_id, second_id),
        ).fetchall()
        if len(rows) != 2:
            raise LookupError("both candidate listings must exist")
        if rows[0]["portal_key"] == rows[1]["portal_key"]:
            raise ValueError("duplicate candidates must come from different portals")

        pair_key = f"{first_id}:{second_id}"
        row = self.connection.execute(
            """
            INSERT INTO duplicate_candidate_groups (
                pair_key, score, reasons_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(pair_key) DO UPDATE SET
                score = excluded.score,
                reasons_json = excluded.reasons_json,
                updated_at = excluded.updated_at
            RETURNING id
            """,
            (pair_key, float(score), serialized_reasons, timestamp, timestamp),
        ).fetchone()
        group_id = row["id"]
        for listing_id in (first_id, second_id):
            self.connection.execute(
                """
                INSERT INTO duplicate_candidate_memberships (
                    group_id, listing_id, added_at
                )
                SELECT ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM duplicate_candidate_memberships
                    WHERE group_id = ? AND listing_id = ? AND removed_at IS NULL
                )
                """,
                (group_id, listing_id, timestamp, group_id, listing_id),
            )
        return group_id

    def get(self, group_id: int) -> DuplicateCandidateGroup:
        row = self.connection.execute(
            "SELECT * FROM duplicate_candidate_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"duplicate candidate group {group_id} does not exist")
        return self._record(row)

    def list(
        self,
        *,
        review_state: DuplicateReviewState | None = None,
        limit: int = 100,
    ) -> tuple[DuplicateCandidateGroup, ...]:
        if review_state is not None and not isinstance(review_state, DuplicateReviewState):
            raise TypeError("review_state must be a DuplicateReviewState or None")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        where = "WHERE review_state = ?" if review_state is not None else ""
        parameters: tuple[object, ...] = (review_state.value,) if review_state else ()
        rows = self.connection.execute(
            f"""
            SELECT * FROM duplicate_candidate_groups
            {where}
            ORDER BY score DESC, datetime(updated_at) DESC, id DESC
            LIMIT ?
            """,  # noqa: S608
            (*parameters, limit),
        ).fetchall()
        return tuple(self._record(row) for row in rows)

    def set_review_state(
        self,
        group_id: int,
        state: DuplicateReviewState,
        *,
        reviewed_at: datetime | None = None,
    ) -> DuplicateCandidateGroup:
        if not isinstance(state, DuplicateReviewState):
            raise TypeError("state must be a DuplicateReviewState")
        timestamp = _utc_timestamp(reviewed_at)
        review_timestamp = None if state is DuplicateReviewState.PENDING else timestamp
        cursor = self.connection.execute(
            """
            UPDATE duplicate_candidate_groups
            SET review_state = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (state.value, review_timestamp, timestamp, group_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"duplicate candidate group {group_id} does not exist")
        return self.get(group_id)

    def is_rejected_pair(self, first_listing_id: int, second_listing_id: int) -> bool:
        first_id, second_id = _validate_pair(first_listing_id, second_listing_id)
        row = self.connection.execute(
            """
            SELECT 1 FROM duplicate_candidate_groups
            WHERE pair_key = ? AND review_state = 'rejected'
            """,
            (f"{first_id}:{second_id}",),
        ).fetchone()
        return row is not None

    def _record(self, row: sqlite3.Row) -> DuplicateCandidateGroup:
        members = self.connection.execute(
            """
            SELECT memberships.listing_id, listings.portal_key, listings.title,
                   listings.canonical_url, memberships.added_at,
                   memberships.removed_at
            FROM duplicate_candidate_memberships AS memberships
            JOIN listings ON listings.id = memberships.listing_id
            WHERE memberships.group_id = ?
            ORDER BY memberships.added_at, memberships.id
            """,
            (row["id"],),
        ).fetchall()
        return DuplicateCandidateGroup(
            id=row["id"],
            pair_key=row["pair_key"],
            score=row["score"],
            reasons=tuple(json.loads(row["reasons_json"])),
            review_state=DuplicateReviewState(row["review_state"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reviewed_at=row["reviewed_at"],
            members=tuple(
                DuplicateCandidateMember(
                    listing_id=member["listing_id"],
                    portal=PortalKey(member["portal_key"]),
                    title=member["title"],
                    canonical_url=member["canonical_url"],
                    added_at=member["added_at"],
                    removed_at=member["removed_at"],
                )
                for member in members
            ),
        )


def _validate_pair(first_listing_id: int, second_listing_id: int) -> tuple[int, int]:
    for listing_id in (first_listing_id, second_listing_id):
        if isinstance(listing_id, bool) or not isinstance(listing_id, int) or listing_id < 1:
            raise ValueError("listing IDs must be positive integers")
    if first_listing_id == second_listing_id:
        raise ValueError("a candidate pair requires two different listings")
    return tuple(sorted((first_listing_id, second_listing_id)))


def _serialize_reasons(reasons: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    if not isinstance(reasons, (tuple, list)) or not reasons:
        raise ValueError("at least one candidate reason is required")
    if any(not isinstance(reason, dict) for reason in reasons):
        raise TypeError("candidate reasons must be objects")
    return json.dumps(reasons, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_timestamp(value: datetime | None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("observed/reviewed time must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _snapshot(row: sqlite3.Row) -> DuplicateListingSnapshot:
    return DuplicateListingSnapshot(
        id=row["id"],
        portal=PortalKey(row["portal_key"]),
        transaction_type=row["transaction_type"],
        property_type=row["property_type"],
        title=row["title"],
        price_euros=row["price_euros"],
        area_sqm=row["area_sqm"],
        rooms=row["rooms"],
        location=row["location"],
        neighbourhood=row["neighbourhood"],
        street=row["street"],
        street_number=row["street_number"],
    )
