import logging
from types import SimpleNamespace

from scrapy.http import HtmlResponse, Request

from scrapyrealestate.spiders.idealista_spider import IdealistaSpider


SEARCH_URL = (
    "https://www.idealista.com/"
    "alquiler-viviendas/madrid-madrid/?ordenado-por=fecha-publicacion-desc"
)


def parse_fixture(html: str) -> list[dict]:
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL,
        request=request,
        body=html.encode(),
        encoding="utf-8",
    )
    spider = IdealistaSpider(start_urls=SEARCH_URL)
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
