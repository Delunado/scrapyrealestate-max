import logging

import pytest
from scrapy.http import HtmlResponse, Request

from scrapyrealestate.spiders.fotocasa_spider import FotocasaSpider


SEARCH_URL = "https://www.fotocasa.es/es/comprar/viviendas/madrid-capital/l"


def parse_html(html: str) -> list[dict]:
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL,
        request=request,
        body=html.encode(),
        encoding="utf-8",
    )
    spider = FotocasaSpider(start_urls=SEARCH_URL)
    return [dict(item) for item in spider.parse(response)]


def test_parse_fotocasa_embedded_json_fields(load_fixture):
    listings = parse_html(load_fixture("fotocasa/search_results.html"))

    assert listings == [
        {
            "id": 87654321,
            "title": "Piso luminoso en Argüelles",
            "price": 289000,
            "rooms": 3,
            "m2": 91,
            "floor": "2ª planta",
            "town": "Madrid",
            "neighbour": "Argüelles",
            "street": "",
            "number": "",
            "type": "buy",
            "href": (
                "https://www.fotocasa.es/es/comprar/vivienda/"
                "madrid-capital/87654321/d"
            ),
            "site": "fotocasa",
        },
        {
            "id": "promo-13579",
            "title": "Promoción de obra nueva",
            "price": "Desde 410.000 €",
            "rooms": "",
            "m2": "",
            "floor": "",
            "town": "",
            "neighbour": "",
            "street": "",
            "number": "",
            "type": "buy",
            "href": "",
            "site": "fotocasa",
        },
    ]


@pytest.mark.parametrize(
    ("html", "warning"),
    [
        ("<html><body></body></html>", "no se encontró el JSON __initial_props__"),
        (
            '<script id="__initial_props__">{not valid json</script>',
            "no se pudo parsear el JSON",
        ),
        (
            '<script id="__initial_props__">{}</script>',
            "no se pudo parsear el JSON",
        ),
    ],
)
def test_parse_fotocasa_missing_or_malformed_payload(html, warning, caplog):
    with caplog.at_level(logging.WARNING):
        listings = parse_html(html)

    assert listings == []
    assert warning in caplog.text
