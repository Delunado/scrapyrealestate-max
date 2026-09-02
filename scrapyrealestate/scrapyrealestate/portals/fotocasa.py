"""Fotocasa adapter around the existing Playwright ``fotocasa`` spider."""

from typing import ClassVar

from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
)
from scrapyrealestate.spiders.fotocasa_spider import FotocasaSpider


class FotocasaAdapter(BasePortalAdapter):
    """Validates Fotocasa search URLs; Fotocasa has no recent-sort suffix."""

    _METADATA: ClassVar[PortalMetadata] = PortalMetadata(
        key=PortalKey.FOTOCASA,
        display_name="Fotocasa",
        domains=frozenset({"fotocasa.es"}),
        spider_name=FotocasaSpider.name,
        transaction_types=frozenset({TransactionType.BUY, TransactionType.RENT}),
        transport=PortalTransport.PLAYWRIGHT,
        capabilities=ALL_LOCAL_CAPABILITIES,
        caveats=(
            "Parses the embedded script#__initial_props__ JSON "
            "(initialSearch.result.realEstates); wait/JSON structure may "
            "change."
        ),
    )

    def _transaction_type(self, raw_url: str) -> TransactionType | None:
        # Mirrors FotocasaSpider.parse, which checks substrings anywhere in
        # the URL rather than a fixed path segment.
        if "alquiler" in raw_url:
            return TransactionType.RENT
        if "comprar" in raw_url or "venta" in raw_url:
            return TransactionType.BUY
        return None

    def _apply_recent_sort(self, raw_url: str) -> str:
        # main.py does not append a recent-sort suffix for Fotocasa.
        return raw_url
