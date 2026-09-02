"""Habitaclia adapter around the existing ``habitaclia`` spider."""

from typing import ClassVar
from urllib.parse import urlsplit

from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
)
from scrapyrealestate.spiders.habitaclia_spider import HabitacliaSpider


class HabitacliaAdapter(BasePortalAdapter):
    """Validates Habitaclia search URLs and sorts them by most recent first."""

    _METADATA: ClassVar[PortalMetadata] = PortalMetadata(
        key=PortalKey.HABITACLIA,
        display_name="Habitaclia",
        domains=frozenset({"habitaclia.com"}),
        spider_name=HabitacliaSpider.name,
        transaction_types=frozenset({TransactionType.BUY, TransactionType.RENT}),
        transport=PortalTransport.HTTP,
        capabilities=ALL_LOCAL_CAPABILITIES,
        caveats=(
            "HTML/CSS selectors; prefers the stable -i<id> detail-URL "
            "identifier and falls back to a canonical-URL fingerprint when "
            "that marker is absent."
        ),
    )

    _TRANSACTION_SEGMENTS: ClassVar[dict[TransactionType, str]] = {
        TransactionType.BUY: "venta",
        TransactionType.RENT: "alquiler",
    }

    def _transaction_type(self, raw_url: str) -> TransactionType | None:
        # Mirrors HabitacliaSpider.parse: the first path segment starts with
        # "venta"/"alquiler", e.g. https://www.habitaclia.com/alquiler-madrid.htm.
        segments = urlsplit(raw_url).path.split("/")
        section = segments[1] if len(segments) > 1 else ""
        prefix = section.split("-")[0]
        if prefix == "alquiler":
            return TransactionType.RENT
        if prefix == "venta":
            return TransactionType.BUY
        return None

    def _apply_recent_sort(self, raw_url: str) -> str:
        # Matches the legacy suffix in main.py: blind concatenation, so a raw
        # URL that already carries a query string would need its own '&'.
        return f"{raw_url}?ordenar=mas_recientes"

    def _build_search_url(self, transaction_type: TransactionType, location_slug: str) -> str:
        # e.g. https://www.habitaclia.com/venta-madrid.htm, matching the
        # "<segment>-<location>.htm" shape used by every raw search URL
        # fixture in this codebase.
        segment = self._TRANSACTION_SEGMENTS[transaction_type]
        return f"https://www.habitaclia.com/{segment}-{location_slug}.htm"
