"""Idempotent listing price history and change detection."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class PriceChange(StrEnum):
    INITIAL = "initial"
    DROP = "drop"
    INCREASE = "increase"
    UNCHANGED = "unchanged"


class PriceObservationConflictError(RuntimeError):
    """The same listing and time were previously recorded with another price."""


@dataclass(frozen=True, slots=True)
class PriceObservationResult:
    change: PriceChange
    previous_price_euros: int | None
    price_euros: int
    recorded: bool


class PriceHistoryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record(
        self,
        listing_id: int,
        price_euros: int,
        observed_at: datetime,
        *,
        currency: str = "EUR",
    ) -> PriceObservationResult:
        timestamp = _timestamp(observed_at)
        existing = self.connection.execute(
            """
            SELECT price_euros FROM listing_price_history
            WHERE listing_id = ? AND observed_at = ?
            """,
            (listing_id, timestamp),
        ).fetchone()
        previous = self.connection.execute(
            """
            SELECT price_euros FROM listing_price_history
            WHERE listing_id = ? AND observed_at < ?
            ORDER BY observed_at DESC, id DESC LIMIT 1
            """,
            (listing_id, timestamp),
        ).fetchone()
        previous_price = previous["price_euros"] if previous is not None else None
        if existing is not None:
            if existing["price_euros"] != price_euros:
                raise PriceObservationConflictError(
                    "price observation already exists with a different price"
                )
            return PriceObservationResult(
                _change(previous_price, price_euros),
                previous_price,
                price_euros,
                False,
            )

        self.connection.execute(
            """
            INSERT INTO listing_price_history (
                listing_id, price_euros, currency, observed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (listing_id, price_euros, currency, timestamp),
        )
        return PriceObservationResult(
            _change(previous_price, price_euros), previous_price, price_euros, True
        )

    def list_for_listing(self, listing_id: int) -> tuple[sqlite3.Row, ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT * FROM listing_price_history
                WHERE listing_id = ? ORDER BY observed_at, id
                """,
                (listing_id,),
            ).fetchall()
        )


def _change(previous_price: int | None, current_price: int) -> PriceChange:
    if previous_price is None:
        return PriceChange.INITIAL
    if current_price < previous_price:
        return PriceChange.DROP
    if current_price > previous_price:
        return PriceChange.INCREASE
    return PriceChange.UNCHANGED


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
