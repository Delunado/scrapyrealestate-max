import pytest
from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import PortalRequest, PortalRequestError, PortalTransport
from scrapyrealestate.portals.fotocasa import FotocasaAdapter
from scrapyrealestate.spiders.fotocasa_spider import FotocasaSpider


def test_fotocasa_metadata_declares_identity_and_playwright_transport():
    metadata = FotocasaAdapter().metadata
    assert metadata.key is PortalKey.FOTOCASA
    assert metadata.domains == frozenset({"fotocasa.es"})
    assert metadata.spider_name == "fotocasa"
    assert metadata.transaction_types == {TransactionType.BUY, TransactionType.RENT}
    assert metadata.transport is PortalTransport.PLAYWRIGHT
    assert metadata.requires_browser is True


@pytest.mark.parametrize(
    ("raw_url", "expected_type"),
    [
        ("https://www.fotocasa.es/es/comprar/viviendas/madrid-capital/l", TransactionType.BUY),
        ("https://www.fotocasa.es/es/alquiler/viviendas/madrid-capital/l", TransactionType.RENT),
    ],
)
def test_fotocasa_build_request_leaves_url_unchanged(raw_url, expected_type):
    request = FotocasaAdapter().build_request(raw_url)

    assert request == PortalRequest(
        portal=PortalKey.FOTOCASA,
        spider_name="fotocasa",
        start_url=raw_url,
        transaction_type=expected_type,
        raw_url=raw_url,
    )


def test_fotocasa_build_request_rejects_wrong_domain():
    with pytest.raises(PortalRequestError, match="does not belong"):
        FotocasaAdapter().build_request(
            "https://www.pisos.com/es/comprar/viviendas/madrid-capital/l"
        )


def test_fotocasa_build_request_rejects_unknown_transaction_section():
    with pytest.raises(PortalRequestError, match="transaction type"):
        FotocasaAdapter().build_request("https://www.fotocasa.es/es/traspaso/madrid-capital/l")


@pytest.mark.parametrize(
    ("transaction_type", "expected_url"),
    [
        (TransactionType.BUY, "https://www.fotocasa.es/es/comprar/viviendas/madrid/l"),
        (TransactionType.RENT, "https://www.fotocasa.es/es/alquiler/viviendas/madrid/l"),
    ],
)
def test_fotocasa_builds_request_from_normalized_search(transaction_type, expected_url):
    search = NormalizedSearch(
        name="Madrid",
        transaction_type=transaction_type,
        filters=SearchFilters(location="Madrid"),
    )

    request = FotocasaAdapter().build_request_from_search(search)

    assert request == PortalRequest(
        portal=PortalKey.FOTOCASA,
        spider_name="fotocasa",
        start_url=expected_url,
        transaction_type=transaction_type,
        raw_url=expected_url,
    )


def test_fotocasa_build_request_from_search_requires_a_location():
    search = NormalizedSearch(name="No location", transaction_type=TransactionType.BUY)

    with pytest.raises(PortalRequestError, match="location filter is required"):
        FotocasaAdapter().build_request_from_search(search)


SEARCH_URL = "https://www.fotocasa.es/es/comprar/viviendas/madrid-capital/l"


def test_fotocasa_normalize_result_matches_legacy_mapper(load_fixture):
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL, request=request,
        body=load_fixture("fotocasa/search_results.html").encode(),
        encoding="utf-8",
    )
    spider = FotocasaSpider(start_urls=SEARCH_URL)
    items = [dict(item) for item in spider.parse(response)]
    adapter = FotocasaAdapter()

    assert items
    for item in items:
        normalized = adapter.normalize_result(item)
        expected = map_legacy_item(item, portal=PortalKey.FOTOCASA)
        assert normalized.portal is PortalKey.FOTOCASA
        assert normalized.external_id == expected.external_id
        assert normalized.canonical_url == expected.canonical_url
        assert normalized.title == expected.title
        assert normalized.price_euros == expected.price_euros
