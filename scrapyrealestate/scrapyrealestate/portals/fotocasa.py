"""Fotocasa adapter around the existing Playwright ``fotocasa`` spider."""

import math
from typing import ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scrapyrealestate.domain.capabilities import SearchFilterKey
from scrapyrealestate.domain.search import NormalizedSearch
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
    remote_capabilities,
)
from scrapyrealestate.spiders.fotocasa_spider import FotocasaSpider


class FotocasaAdapter(BasePortalAdapter):
    """Validate Fotocasa URLs and request newest listings first."""

    _METADATA: ClassVar[PortalMetadata] = PortalMetadata(
        key=PortalKey.FOTOCASA,
        display_name="Fotocasa",
        domains=frozenset({"fotocasa.es"}),
        spider_name=FotocasaSpider.name,
        transaction_types=frozenset({TransactionType.BUY, TransactionType.RENT}),
        transport=PortalTransport.PLAYWRIGHT,
        capabilities=remote_capabilities(
            SearchFilterKey.LOCATION,
            SearchFilterKey.MIN_PRICE_EUROS,
            SearchFilterKey.MAX_PRICE_EUROS,
            SearchFilterKey.MIN_AREA_SQM,
            SearchFilterKey.MAX_AREA_SQM,
            SearchFilterKey.MIN_ROOMS,
            SearchFilterKey.MAX_ROOMS,
        ),
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
        parsed = urlsplit(raw_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(sortType="publicationDate", sortOrderDesc="true")
        return urlunsplit(parsed._replace(query=urlencode(query)))

    def _build_search_url(self, search: NormalizedSearch, location_slug: str) -> str:
        # e.g. https://www.fotocasa.es/es/comprar/viviendas/madrid/l, matching
        # the "/es/<segment>/viviendas/<location>/l" shape used by this
        # codebase's raw search URL fixtures (SEARCH_URL below). Fotocasa's
        # own taxonomy sometimes prefers a "<city>-capital" location code
        # instead of the plain municipality slug for a provincial capital;
        # that distinction is out of scope for this best-effort slug (see
        # portals.location), so an exact-match search should keep using the
        # raw-URL override instead.
        location_path = {
            "malaga": "malaga-capital/todas-las-zonas",
            "madrid": "madrid-capital/todas-las-zonas",
        }.get(location_slug, location_slug)
        segment = self._TRANSACTION_SEGMENTS[search.transaction_type]
        base = f"https://www.fotocasa.es/es/{segment}/viviendas/{location_path}/l/1"
        filters = search.filters
        query = {
            key: str(value)
            for key, value in (
                ("minPrice", filters.min_price_euros),
                ("maxPrice", filters.max_price_euros),
                (
                    "minSurface",
                    math.floor(filters.min_area_sqm)
                    if filters.min_area_sqm is not None
                    else None,
                ),
                (
                    "maxSurface",
                    math.ceil(filters.max_area_sqm)
                    if filters.max_area_sqm is not None
                    else None,
                ),
                ("minRooms", filters.min_rooms),
                ("maxRooms", filters.max_rooms),
            )
            if value is not None
        }
        return f"{base}?{urlencode(query)}" if query else base
