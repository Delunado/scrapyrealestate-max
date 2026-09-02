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

    _TRANSACTION_SEGMENTS: ClassVar[dict[TransactionType, str]] = {
        TransactionType.BUY: "comprar",
        TransactionType.RENT: "alquiler",
    }

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

    def _build_search_url(self, transaction_type: TransactionType, location_slug: str) -> str:
        # e.g. https://www.fotocasa.es/es/comprar/viviendas/madrid/l, matching
        # the "/es/<segment>/viviendas/<location>/l" shape used by this
        # codebase's raw search URL fixtures (SEARCH_URL below). Fotocasa's
        # own taxonomy sometimes prefers a "<city>-capital" location code
        # instead of the plain municipality slug for a provincial capital;
        # that distinction is out of scope for this best-effort slug (see
        # portals.location), so an exact-match search should keep using the
        # raw-URL override instead.
        segment = self._TRANSACTION_SEGMENTS[transaction_type]
        return f"https://www.fotocasa.es/es/{segment}/viviendas/{location_slug}/l"
