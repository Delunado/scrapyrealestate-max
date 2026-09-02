"""Idealista adapters around the existing ``idealista``/``idealista_proxy`` spiders.

Both variants default to ``degraded=True``: DataDome commonly blocks headless
automation and public rotating proxies are inherently unreliable, so neither
adapter promises an anti-bot bypass. Callers must treat Idealista health as
independent of overall system health, per AGENTS.md.

Neither adapter overrides ``_build_search_url``: Idealista's location
taxonomy is a ``<province>-<municipality>`` pair (e.g. the "madrid-madrid"
below), not one this codebase can safely derive from a single free-text
location without a province lookup table it does not have. Guessing
``"<slug>-<slug>"`` would silently misroute any municipality whose province
has a different name. ``build_request_from_search`` therefore raises the
shared ``BasePortalAdapter`` "not implemented" error rather than a best-effort
guess; the legacy-compatible ``build_request(raw_url)`` path is unaffected.
"""

from typing import ClassVar
from urllib.parse import urlsplit

from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
)
from scrapyrealestate.spiders.idealista_spider import IdealistaSpider
from scrapyrealestate.spiders.idealista_spider_proxy import IdealistaProxySpider


class IdealistaAdapter(BasePortalAdapter):
    """Validates Idealista search URLs; degraded because DataDome may block it."""

    _METADATA: ClassVar[PortalMetadata] = PortalMetadata(
        key=PortalKey.IDEALISTA,
        display_name="Idealista",
        domains=frozenset({"idealista.com"}),
        spider_name=IdealistaSpider.name,
        transaction_types=frozenset({TransactionType.BUY, TransactionType.RENT}),
        transport=PortalTransport.PLAYWRIGHT,
        capabilities=ALL_LOCAL_CAPABILITIES,
        caveats=(
            "DataDome commonly blocks headless automation; treat as "
            "externally unreliable. No anti-bot bypass is guaranteed."
        ),
        degraded=True,
    )

    def _transaction_type(self, raw_url: str) -> TransactionType | None:
        # Mirrors idealista_spider._transaction_type: the first path segment
        # starts with "venta"/"alquiler", e.g.
        # https://www.idealista.com/alquiler-viviendas/madrid-madrid/.
        segments = urlsplit(raw_url).path.split("/")
        section = segments[1] if len(segments) > 1 else ""
        prefix = section.split("-")[0]
        if prefix == "alquiler":
            return TransactionType.RENT
        if prefix == "venta":
            return TransactionType.BUY
        return None

    def _apply_recent_sort(self, raw_url: str) -> str:
        # Matches the legacy suffix in main.py.
        return f"{raw_url}?ordenado-por=fecha-publicacion-desc"


class IdealistaProxyAdapter(IdealistaAdapter):
    """Idealista through rotating public proxies instead of Playwright.

    Reuses ``IdealistaAdapter``'s URL validation and recent-sort construction
    (identical search URL shape); only the transport metadata and portal
    identity differ, matching ``idealista_spider_proxy`` reusing the primary
    spider's parser.
    """

    _METADATA: ClassVar[PortalMetadata] = PortalMetadata(
        key=PortalKey.IDEALISTA_PROXY,
        display_name="Idealista (proxy)",
        domains=frozenset({"idealista.com"}),
        spider_name=IdealistaProxySpider.name,
        transaction_types=frozenset({TransactionType.BUY, TransactionType.RENT}),
        transport=PortalTransport.ROTATING_PROXY_HTTP,
        capabilities=ALL_LOCAL_CAPABILITIES,
        caveats=(
            "Public proxy discovery is slow/unreliable and is not a "
            "supported anti-bot guarantee."
        ),
        degraded=True,
    )
