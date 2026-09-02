"""Normalized domain types shared by scraping, persistence, and services."""

from scrapyrealestate.domain.listing import NormalizedListing, canonicalize_url

from scrapyrealestate.domain.values import (
    PortalKey,
    PropertyType,
    RunStatus,
    TransactionType,
    TriState,
)

__all__ = [
    "NormalizedListing",
    "PortalKey",
    "PropertyType",
    "RunStatus",
    "TransactionType",
    "TriState",
    "canonicalize_url",
]
