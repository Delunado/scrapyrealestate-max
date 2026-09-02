import pytest
from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import PortalRequest, PortalRequestError, PortalTransport
from scrapyrealestate.portals.yaencontre import YaencontreAdapter
from scrapyrealestate.spiders.yaencontre_spider import YaencontreSpider


def test_yaencontre_metadata_declares_identity_and_playwright_transport():
    metadata = YaencontreAdapter().metadata
    assert metadata.key is PortalKey.YAENCONTRE
    assert metadata.domains == frozenset({"yaencontre.com"})
    assert metadata.spider_name == "yaencontre"
    assert metadata.transaction_types == {TransactionType.BUY, TransactionType.RENT}
    assert metadata.transport is PortalTransport.PLAYWRIGHT
    assert metadata.requires_browser is True


@pytest.mark.parametrize(
    ("raw_url", "expected_type"),
    [
        ("https://www.yaencontre.com/alquiler/pisos/madrid", TransactionType.RENT),
        ("https://www.yaencontre.com/comprar/pisos/madrid", TransactionType.BUY),
    ],
)
def test_yaencontre_build_request_applies_recent_sort(raw_url, expected_type):
    request = YaencontreAdapter().build_request(raw_url)

    assert request == PortalRequest(
        portal=PortalKey.YAENCONTRE,
        spider_name="yaencontre",
        start_url=f"{raw_url}/o-recientes",
        transaction_type=expected_type,
        raw_url=raw_url,
    )


def test_yaencontre_build_request_rejects_wrong_domain():
    with pytest.raises(PortalRequestError, match="does not belong"):
        YaencontreAdapter().build_request("https://www.pisos.com/alquiler/pisos/madrid")


def test_yaencontre_build_request_rejects_unknown_transaction_section():
    with pytest.raises(PortalRequestError, match="transaction type"):
        YaencontreAdapter().build_request("https://www.yaencontre.com/traspaso/pisos/madrid")


SEARCH_URL = "https://www.yaencontre.com/alquiler/pisos/madrid/o-recientes"


def test_yaencontre_normalize_result_matches_legacy_mapper(load_fixture):
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL, request=request,
        body=load_fixture("yaencontre/search_results.html").encode(),
        encoding="utf-8",
    )
    spider = YaencontreSpider(start_urls=SEARCH_URL)
    items = [dict(item) for item in spider.parse(response)]
    adapter = YaencontreAdapter()

    assert items
    for item in items:
        normalized = adapter.normalize_result(item)
        expected = map_legacy_item(item, portal=PortalKey.YAENCONTRE)
        assert normalized.portal is PortalKey.YAENCONTRE
        assert normalized.external_id == expected.external_id
        assert normalized.canonical_url == expected.canonical_url
        assert normalized.title == expected.title
        assert normalized.price_euros == expected.price_euros
