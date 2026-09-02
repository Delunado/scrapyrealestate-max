import pytest
from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import PortalRequest, PortalRequestError, PortalTransport
from scrapyrealestate.portals.pisoscom import PisoscomAdapter
from scrapyrealestate.spiders.pisoscom_spider import PisoscomSpider


def test_pisoscom_metadata_declares_identity_and_transport():
    metadata = PisoscomAdapter().metadata
    assert metadata.key is PortalKey.PISOSCOM
    assert metadata.domains == frozenset({"pisos.com"})
    assert metadata.spider_name == "pisoscom"
    assert metadata.transaction_types == {TransactionType.BUY, TransactionType.RENT}
    assert metadata.transport is PortalTransport.HTTP
    assert metadata.requires_browser is False
    assert metadata.degraded is False


@pytest.mark.parametrize(
    ("raw_url", "expected_type"),
    [
        ("https://www.pisos.com/venta/pisos-madrid/", TransactionType.BUY),
        ("https://www.pisos.com/alquiler/pisos-madrid/", TransactionType.RENT),
    ],
)
def test_pisoscom_build_request_applies_recent_sort(raw_url, expected_type):
    request = PisoscomAdapter().build_request(raw_url)

    assert request == PortalRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url=f"{raw_url}fecharecientedesde-desc/",
        transaction_type=expected_type,
        raw_url=raw_url,
    )


def test_pisoscom_build_request_rejects_wrong_domain():
    with pytest.raises(PortalRequestError, match="does not belong"):
        PisoscomAdapter().build_request("https://www.habitaclia.com/venta/pisos-madrid/")


def test_pisoscom_build_request_rejects_unknown_transaction_section():
    with pytest.raises(PortalRequestError, match="transaction type"):
        PisoscomAdapter().build_request("https://www.pisos.com/traspaso/pisos-madrid/")


SEARCH_URL = "https://www.pisos.com/venta/pisos-madrid/fecharecientedesde-desc/"


def test_pisoscom_normalize_result_matches_legacy_mapper(load_fixture):
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL, request=request,
        body=load_fixture("pisoscom/search_results.html").encode(),
        encoding="utf-8",
    )
    spider = PisoscomSpider(start_urls=SEARCH_URL)
    items = [dict(item) for item in spider.parse(response)]
    adapter = PisoscomAdapter()

    for item in items:
        normalized = adapter.normalize_result(item)
        expected = map_legacy_item(item, portal=PortalKey.PISOSCOM)
        assert normalized.portal is PortalKey.PISOSCOM
        assert normalized.external_id == expected.external_id
        assert normalized.canonical_url == expected.canonical_url
        assert normalized.title == expected.title
        assert normalized.price_euros == expected.price_euros
