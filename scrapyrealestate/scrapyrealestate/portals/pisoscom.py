"""Pisos.com adapter around the existing ``pisoscom`` spider."""

import math
from typing import ClassVar
from urllib.parse import urlsplit

from scrapyrealestate.domain.capabilities import SearchFilterKey
from scrapyrealestate.domain.search import NormalizedSearch
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
    remote_capabilities,
)
from scrapyrealestate.spiders.pisoscom_spider import PisoscomSpider


class PisoscomAdapter(BasePortalAdapter):
    """Validates Pisos.com search URLs and sorts them by most recent first."""

    _METADATA: ClassVar[PortalMetadata] = PortalMetadata(
        key=PortalKey.PISOSCOM,
        display_name="Pisos.com",
        domains=frozenset({"pisos.com"}),
        spider_name=PisoscomSpider.name,
        transaction_types=frozenset({TransactionType.BUY, TransactionType.RENT}),
        transport=PortalTransport.HTTP,
        capabilities=remote_capabilities(
            SearchFilterKey.LOCATION,
            SearchFilterKey.MIN_PRICE_EUROS,
            SearchFilterKey.MAX_PRICE_EUROS,
            SearchFilterKey.MIN_AREA_SQM,
            SearchFilterKey.MIN_ROOMS,
        ),
        caveats=(
            "HTML/CSS selectors; currently considered the simplest maintained "
            "target."
        ),
    )

    _TRANSACTION_SEGMENTS: ClassVar[dict[TransactionType, str]] = {
        TransactionType.BUY: "venta",
        TransactionType.RENT: "alquiler",
    }

    def _transaction_type(self, raw_url: str) -> TransactionType | None:
        # Mirrors PisoscomSpider.parse: the first path segment is
        # "venta"/"alquiler", e.g. https://www.pisos.com/venta/pisos-madrid/.
        segments = urlsplit(raw_url).path.split("/")
        section = segments[1] if len(segments) > 1 else ""
        if section == "alquiler":
            return TransactionType.RENT
        if section == "venta":
            return TransactionType.BUY
        return None

    def _apply_recent_sort(self, raw_url: str) -> str:
        # Matches the legacy suffix in main.py: the raw URL is expected to
        # already end in "/".
        return f"{raw_url}fecharecientedesde-desc/"

    def _build_search_url(self, search: NormalizedSearch, location_slug: str) -> str:
        # Pisos.com otherwise interprets the ambiguous `pisos-malaga` path as
        # the whole province. Its municipality taxonomy uses this explicit slug.
        location_slug = {"malaga": "malaga_capital_zona_urbana"}.get(
            location_slug, location_slug
        )
        segment = self._TRANSACTION_SEGMENTS[search.transaction_type]
        parts = [f"https://www.pisos.com/{segment}/pisos-{location_slug}"]
        filters = search.filters
        if filters.min_price_euros is not None:
            parts.append(f"desde-{filters.min_price_euros}")
        if filters.max_price_euros is not None:
            parts.append(f"hasta-{filters.max_price_euros}")
        if filters.min_rooms is not None:
            parts.append(f"con-{filters.min_rooms}-habitaciones")
        if filters.min_area_sqm is not None:
            parts.append(f"desde-{math.floor(filters.min_area_sqm)}-m2")
        return "/".join(parts) + "/"
