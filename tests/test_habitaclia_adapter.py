import pytest
from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import PortalRequest, PortalRequestError, PortalTransport
from scrapyrealestate.portals.habitaclia import HabitacliaAdapter
from scrapyrealestate.spiders.habitaclia_spider import HabitacliaSpider


def test_habitaclia_metadata_declares_identity_and_transport():
    metadata = HabitacliaAdapter().metadata
    assert metadata.key is PortalKey.HABITACLIA
    assert metadata.domains == frozenset({"habitaclia.com"})
    assert metadata.spider_name == "habitaclia"
    assert metadata.transaction_types == {TransactionType.BUY, TransactionType.RENT}
    assert metadata.transport is PortalTransport.HTTP
    assert metadata.requires_browser is False


@pytest.mark.parametrize(
    ("raw_url", "expected_type"),
    [
        ("https://www.habitaclia.com/alquiler-madrid.htm", TransactionType.RENT),
        ("https://www.habitaclia.com/venta-pisos-madrid.htm", TransactionType.BUY),
    ],
)
def test_habitaclia_build_request_applies_recent_sort(raw_url, expected_type):
    request = HabitacliaAdapter().build_request(raw_url)

    assert request == PortalRequest(
        portal=PortalKey.HABITACLIA,
        spider_name="habitaclia",
        start_url=f"{raw_url}?ordenar=mas_recientes",
        transaction_type=expected_type,
        raw_url=raw_url,
    )


def test_habitaclia_build_request_rejects_wrong_domain():
    with pytest.raises(PortalRequestError, match="does not belong"):
        HabitacliaAdapter().build_request("https://www.pisos.com/alquiler-madrid.htm")


def test_habitaclia_build_request_rejects_unknown_transaction_section():
    with pytest.raises(PortalRequestError, match="transaction type"):
        HabitacliaAdapter().build_request("https://www.habitaclia.com/traspaso-madrid.htm")


SEARCH_URL = "https://www.habitaclia.com/alquiler-madrid.htm?ordenar=mas_recientes"


def test_habitaclia_normalize_result_matches_legacy_mapper(load_fixture):
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL, request=request,
        body=load_fixture("habitaclia/search_results.html").encode(),
        encoding="utf-8",
    )
    spider = HabitacliaSpider(start_urls=SEARCH_URL)
    items = [dict(item) for item in spider.parse(response)]
    adapter = HabitacliaAdapter()

    for item in items:
        normalized = adapter.normalize_result(item)
        expected = map_legacy_item(item, portal=PortalKey.HABITACLIA)
        assert normalized.portal is PortalKey.HABITACLIA
        assert normalized.external_id == expected.external_id
        assert normalized.canonical_url == expected.canonical_url
        assert normalized.title == expected.title
        assert normalized.price_euros == expected.price_euros
