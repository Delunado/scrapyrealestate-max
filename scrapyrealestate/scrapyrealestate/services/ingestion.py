"""Transactional ingestion of one conclusive portal attempt.

``IngestionService.ingest_attempt`` is the seam between a portal attempt's
already-normalized, locally-filtered listings (produced by
``services.search_orchestration``) and persisted state. For every listing
it upserts the listing and its per-search visibility
(``ListingMatchRepository``), records a price observation when a price is
known (``PriceHistoryRepository``), and raises provider-neutral change
events for new listings, price drops, price increases, and reappearances
(``NotificationRepository``). It then reconciles which of the search's
previously-active listings on this portal were not seen this time.

All of it commits or rolls back as one unit of work: a malformed or
identity-conflicting listing partway through a batch must not leave
earlier listings in that same batch persisted while the rest are lost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.values import PortalKey, RunStatus
from scrapyrealestate.persistence.database import transaction
from scrapyrealestate.persistence.listings import (
    DisappearanceResult,
    ListingMatchOutcome,
    ListingMatchRepository,
)
from scrapyrealestate.persistence.notifications import (
    NotificationEventRecord,
    NotificationEventType,
    NotificationRepository,
)
from scrapyrealestate.persistence.prices import PriceChange, PriceHistoryRepository

_NO_DISAPPEARANCE = DisappearanceResult(reconciled=False)


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    """What one portal attempt's ingestion did, for run/attempt counts."""

    new: int = 0
    changed: int = 0
    reappeared: int = 0
    unchanged: int = 0
    listing_ids: tuple[int, ...] = ()
    events: tuple[NotificationEventRecord, ...] = ()
    disappearance: DisappearanceResult = _NO_DISAPPEARANCE


class IngestionService:
    """Ingests one portal attempt's matched listings as one transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._listings = ListingMatchRepository(connection)
        self._prices = PriceHistoryRepository(connection)
        self._notifications = NotificationRepository(connection)

    def ingest_attempt(
        self,
        search_id: int,
        portal: PortalKey,
        listings: tuple[NormalizedListing, ...],
        status: RunStatus,
    ) -> IngestionOutcome:
        """Ingest every listing this portal attempt matched, atomically.

        ``status`` gates disappearance reconciliation exactly as
        ``ListingMatchRepository.reconcile_portal`` already does: only a
        conclusive (success/empty) attempt may mark previously-active
        listings on this portal absent.
        """
        for listing in listings:
            if listing.portal is not portal:
                raise ValueError(
                    f"listing portal {listing.portal.value!r} does not match "
                    f"attempt portal {portal.value!r}"
                )

        with transaction(self.connection, immediate=True):
            tallies = dict.fromkeys(ListingMatchOutcome, 0)
            listing_ids: list[int] = []
            events: list[NotificationEventRecord] = []

            for listing in listings:
                result = self._listings.ingest_locked(search_id, listing)
                tallies[result.outcome] += 1
                listing_ids.append(result.listing_id)
                events.extend(
                    self._events_for(
                        search_id, portal, result.listing_id, result.outcome, listing
                    )
                )

            disappearance = self._listings.reconcile_portal_locked(
                search_id, portal, frozenset(listing_ids), status
            )
            return IngestionOutcome(
                new=tallies[ListingMatchOutcome.NEW],
                changed=tallies[ListingMatchOutcome.CHANGED],
                reappeared=tallies[ListingMatchOutcome.REAPPEARED],
                unchanged=tallies[ListingMatchOutcome.UNCHANGED],
                listing_ids=tuple(listing_ids),
                events=tuple(events),
                disappearance=disappearance,
            )

    def _events_for(
        self,
        search_id: int,
        portal: PortalKey,
        listing_id: int,
        outcome: ListingMatchOutcome,
        listing: NormalizedListing,
    ) -> list[NotificationEventRecord]:
        events: list[NotificationEventRecord] = []

        if outcome is ListingMatchOutcome.NEW:
            events.append(
                self._create_event(
                    search_id,
                    NotificationEventType.NEW_LISTING,
                    f"new_listing:{portal.value}:{listing_id}",
                    listing.observed_at,
                    listing_id,
                    {"price_euros": listing.price_euros},
                )
            )
        elif outcome is ListingMatchOutcome.REAPPEARED:
            events.append(
                self._create_event(
                    search_id,
                    NotificationEventType.REAPPEARANCE,
                    f"reappearance:{portal.value}:{listing_id}:"
                    f"{_timestamp(listing.observed_at)}",
                    listing.observed_at,
                    listing_id,
                    {"price_euros": listing.price_euros},
                )
            )

        if listing.price_euros is not None:
            price_result = self._prices.record(
                listing_id, listing.price_euros, listing.observed_at
            )
            if price_result.change in (PriceChange.DROP, PriceChange.INCREASE):
                event_type = (
                    NotificationEventType.PRICE_DROP
                    if price_result.change is PriceChange.DROP
                    else NotificationEventType.PRICE_INCREASE
                )
                events.append(
                    self._create_event(
                        search_id,
                        event_type,
                        f"{event_type.value}:{portal.value}:{listing_id}:"
                        f"{listing.price_euros}",
                        listing.observed_at,
                        listing_id,
                        {
                            "previous_price_euros": price_result.previous_price_euros,
                            "price_euros": price_result.price_euros,
                        },
                    )
                )
        return events

    def _create_event(
        self,
        search_id: int,
        event_type: NotificationEventType,
        deduplication_key: str,
        occurred_at: datetime,
        listing_id: int,
        payload: dict[str, Any],
    ) -> NotificationEventRecord:
        creation = self._notifications.create_event(
            search_id,
            event_type,
            deduplication_key,
            occurred_at,
            listing_id=listing_id,
            payload=payload,
        )
        if creation.created:
            # This method runs inside ingest_attempt's outer transaction, so
            # event creation and its initial per-channel delivery attempts are
            # committed or rolled back together.
            self._notifications.ensure_event_deliveries(creation.event.id)
        return creation.event


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
