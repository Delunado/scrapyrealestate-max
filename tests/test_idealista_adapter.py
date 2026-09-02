import pytest
from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import PortalRequest, PortalRequestError, PortalTransport
from scrapyrealestate.portals.idealista import IdealistaAdapter, IdealistaProxyAdapter
from scrapyrealestate.spiders.idealista_spider import IdealistaSpider


@pytest.mark.parametrize("adapter_class", [IdealistaAdapter, IdealistaProxyAdapter])
def test_idealista_adapters_default_to_degraded_status(adapter_class):
    metadata = adapter_class().metadata
    assert metadata.degraded is True
    assert metadata.domains == frozenset({"idealista.com"})
    assert metadata.transaction_types == {TransactionType.BUY, TransactionType.RENT}


def test_idealista_metadata_uses_playwright_transport():
    metadata = IdealistaAdapter().metadata
    assert metadata.key is PortalKey.IDEALISTA
    assert metadata.spider_name == "idealista"
    assert metadata.transport is PortalTransport.PLAYWRIGHT
    assert metadata.requires_browser is True


def test_idealista_proxy_metadata_uses_rotating_proxy_transport():
    metadata = IdealistaProxyAdapter().metadata
    assert metadata.key is PortalKey.IDEALISTA_PROXY
    assert metadata.spider_name == "idealista_proxy"
    assert metadata.transport is PortalTransport.ROTATING_PROXY_HTTP
    # A rotating-proxy HTTP transport does not launch Chromium.
    assert metadata.requires_browser is False


@pytest.mark.parametrize("adapter_class", [IdealistaAdapter, IdealistaProxyAdapter])
@pytest.mark.parametrize(
    ("raw_url", "expected_type"),
    [
        (
            "https://www.idealista.com/alquiler-viviendas/madrid-madrid/",
            TransactionType.RENT,
        ),
        (
            "https://www.idealista.com/venta-viviendas/madrid-madrid/",
            TransactionType.BUY,
        ),
    ],
)
def test_idealista_build_request_applies_recent_sort(adapter_class, raw_url, expected_type):
    adapter = adapter_class()
    request = adapter.build_request(raw_url)

    assert request == PortalRequest(
        portal=adapter.metadata.key,
        spider_name=adapter.metadata.spider_name,
        start_url=f"{raw_url}?ordenado-por=fecha-publicacion-desc",
        transaction_type=expected_type,
        raw_url=raw_url,
    )


@pytest.mark.parametrize("adapter_class", [IdealistaAdapter, IdealistaProxyAdapter])
def test_idealista_build_request_rejects_wrong_domain(adapter_class):
    with pytest.raises(PortalRequestError, match="does not belong"):
        adapter_class().build_request("https://www.pisos.com/alquiler-viviendas/madrid-madrid/")


@pytest.mark.parametrize("adapter_class", [IdealistaAdapter, IdealistaProxyAdapter])
def test_idealista_build_request_rejects_unknown_transaction_section(adapter_class):
    with pytest.raises(PortalRequestError, match="transaction type"):
        adapter_class().build_request("https://www.idealista.com/traspaso-viviendas/madrid/")


@pytest.mark.parametrize("adapter_class", [IdealistaAdapter, IdealistaProxyAdapter])
def test_idealista_build_request_from_search_is_explicitly_unsupported(adapter_class):
    # Idealista's real location taxonomy is a <province>-<municipality> pair
    # (e.g. "madrid-madrid" above), not derivable from one free-text location
    # string without a lookup table this adapter does not have. Guessing
    # "<slug>-<slug>" would be silently wrong for any municipality whose
    # province has a different name (e.g. Getafe, in Madrid province), so
    # this adapter relies on the shared BasePortalAdapter default instead of
    # implementing its own _build_search_url -- explicit unsupported, not a
    # best-effort guess, per AGENTS.md.
    search = NormalizedSearch(
        name="Madrid",
        transaction_type=TransactionType.BUY,
        filters=SearchFilters(location="Madrid"),
    )
    with pytest.raises(PortalRequestError, match="not implemented"):
        adapter_class().build_request_from_search(search)


SEARCH_URL = (
    "https://www.idealista.com/"
    "alquiler-viviendas/madrid-madrid/?ordenado-por=fecha-publicacion-desc"
)


def test_idealista_normalize_result_matches_legacy_mapper(load_fixture):
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL, request=request,
        body=load_fixture("idealista/search_results.html").encode(),
        encoding="utf-8",
    )
    spider = IdealistaSpider(start_urls=SEARCH_URL)
    items = [dict(item) for item in spider.parse(response)]
    adapter = IdealistaAdapter()

    assert items
    for item in items:
        normalized = adapter.normalize_result(item)
        expected = map_legacy_item(item, portal=PortalKey.IDEALISTA)
        assert normalized.portal is PortalKey.IDEALISTA
        assert normalized.external_id == expected.external_id
        assert normalized.canonical_url == expected.canonical_url
        assert normalized.title == expected.title
        assert normalized.price_euros == expected.price_euros


def test_idealista_proxy_normalize_result_uses_its_own_portal_identity(load_fixture):
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL, request=request,
        body=load_fixture("idealista/search_results.html").encode(),
        encoding="utf-8",
    )
    spider = IdealistaSpider(start_urls=SEARCH_URL)
    items = [dict(item) for item in spider.parse(response)]
    adapter = IdealistaProxyAdapter()

    assert items
    normalized = adapter.normalize_result(items[0])
    assert normalized.portal is PortalKey.IDEALISTA_PROXY
