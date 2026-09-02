"""Yaencontre adapter around the existing Playwright ``yaencontre`` spider."""

from typing import ClassVar

from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
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
        capabilities=ALL_LOCAL_CAPABILITIES,
        caveats=(
            "Plain requests have returned 403; relies on rendered "
            "article.real-estate-card selectors."
        ),
    )

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
