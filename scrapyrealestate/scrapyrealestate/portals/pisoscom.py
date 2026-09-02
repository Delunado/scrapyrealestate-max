"""Pisos.com adapter around the existing ``pisoscom`` spider."""

from typing import ClassVar
from urllib.parse import urlsplit

from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
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
        capabilities=ALL_LOCAL_CAPABILITIES,
        caveats=(
            "HTML/CSS selectors; currently considered the simplest maintained "
            "target."
        ),
    )

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
