import logging
from types import SimpleNamespace

from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.spiders.idealista_spider import IdealistaSpider
from scrapyrealestate.spiders.idealista_spider_proxy import IdealistaProxySpider


SEARCH_URL = (
    "https://www.idealista.com/"
    "alquiler-viviendas/madrid-madrid/?ordenado-por=fecha-publicacion-desc"
)


def parse_fixture(html: str, spider_class=IdealistaSpider) -> list[dict]:
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL,
        request=request,
        body=html.encode(),
        encoding="utf-8",
    )
    spider = spider_class(start_urls=SEARCH_URL)
    return [dict(item) for item in spider.parse(response)]


def test_parse_idealista_datadome_challenge_is_empty_and_warns(
    load_fixture, caplog
):
    with caplog.at_level(logging.WARNING):
        listings = parse_fixture(load_fixture("idealista/datadome_challenge.html"))

    assert listings == []
    assert "posible bloqueo anti-bot" in caplog.text


def test_idealista_playwright_error_path_is_logged(caplog):
    spider = IdealistaSpider(start_urls=SEARCH_URL)
    failure = SimpleNamespace(value=RuntimeError("selector wait timed out"))

    with caplog.at_level(logging.ERROR):
        spider.on_error(failure)

    assert "Error al obtener datos de idealista.com" in caplog.text
    assert "selector wait timed out" in caplog.text


def test_idealista_fixture_results_satisfy_normalized_boundary(load_fixture):
    items = parse_fixture(load_fixture("idealista/search_results.html"))
    normalized = [map_legacy_item(item) for item in items]

    assert len(normalized) == 2
    listing = normalized[0]
    assert listing.portal is PortalKey.IDEALISTA
    assert listing.transaction_type is TransactionType.RENT
    assert listing.external_id == "135791357"
    assert listing.price_euros == 315_000
    assert listing.area_sqm == 96
    assert listing.rooms == 3
    assert listing.floor == 4
    assert listing.location == "Madrid"
    assert listing.neighbourhood == "Goya"
    assert listing.street == "Calle de Alcalá"
    assert listing.street_number == "12"
    assert normalized[1].price_euros is None


def test_idealista_proxy_uses_its_own_portal_identity(load_fixture):
    items = parse_fixture(
        load_fixture("idealista/search_results.html"), IdealistaProxySpider
    )

    assert all(item["site"] == "idealista_proxy" for item in items)
    assert all(
        map_legacy_item(item).portal is PortalKey.IDEALISTA_PROXY for item in items
    )
