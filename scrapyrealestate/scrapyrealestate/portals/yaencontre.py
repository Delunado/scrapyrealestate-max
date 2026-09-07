"""Yaencontre adapter around the existing Playwright ``yaencontre`` spider."""

from typing import ClassVar

from scrapyrealestate.domain.capabilities import SearchFilterKey
from scrapyrealestate.domain.search import NormalizedSearch
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
    remote_capabilities,
)
from scrapyrealestate.spiders.yaencontre_spider import YaencontreSpider


class YaencontreAdapter(BasePortalAdapter):
    """Validates Yaencontre search URLs and sorts them by most recent first."""

    _METADATA: ClassVar[PortalMetadata] = PortalMetadata(
        key=PortalKey.YAENCONTRE,
        display_name="Yaencontre",
        domains=frozenset({"yaencontre.com"}),
        spider_name=YaencontreSpider.name,
        transaction_types=frozenset({TransactionType.BUY, TransactionType.RENT}),
        transport=PortalTransport.PLAYWRIGHT,
        capabilities=remote_capabilities(SearchFilterKey.LOCATION),
        caveats=(
            "Plain requests have returned 403; relies on rendered "
            "article.real-estate-card selectors."
        ),
    )

    _TRANSACTION_SEGMENTS: ClassVar[dict[TransactionType, str]] = {
        TransactionType.BUY: "venta",
        TransactionType.RENT: "alquiler",
    }

    def _transaction_type(self, raw_url: str) -> TransactionType | None:
        # Mirrors YaencontreSpider.parse, which checks substrings anywhere in
        # the URL rather than a fixed path segment.
        if "alquiler" in raw_url:
            return TransactionType.RENT
        if "comprar" in raw_url or "venta" in raw_url:
            return TransactionType.BUY
        return None

    def _apply_recent_sort(self, raw_url: str) -> str:
        # Matches the legacy suffix in main.py.
        return f"{raw_url}/o-recientes"

    def _build_search_url(self, search: NormalizedSearch, location_slug: str) -> str:
        # e.g. https://www.yaencontre.com/comprar/pisos/madrid, matching the
        # "<segment>/pisos/<location>" shape used by every raw search URL
        # fixture in this codebase.
        segment = self._TRANSACTION_SEGMENTS[search.transaction_type]
        return f"https://www.yaencontre.com/{segment}/pisos/{location_slug}"
