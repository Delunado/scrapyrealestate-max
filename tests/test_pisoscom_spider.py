from scrapy.http import HtmlResponse, Request

from scrapyrealestate.spiders.pisoscom_spider import PisoscomSpider


SEARCH_URL = "https://www.pisos.com/venta/pisos-madrid/fecharecientedesde-desc/"


def parse_fixture(html: str) -> list[dict]:
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL,
        request=request,
        body=html.encode(),
        encoding="utf-8",
    )
    spider = PisoscomSpider(start_urls=SEARCH_URL)
    return [dict(item) for item in spider.parse(response)]


def test_parse_pisoscom_result_fields(load_fixture):
    listings = parse_fixture(load_fixture("pisoscom/search_results.html"))

    assert listings[0] == {
        "id": "12345678901",
        "price": "325.000 €",
        "m2": "92 m²",
        "rooms": "3 hab.",
        "floor": "2ª planta",
        "town": "Madrid",
        "neighbour": "Sol",
        "street": "Piso en calle de Alcalá",
        "number": " 12",
        "type": "buy",
        "title": "Piso en calle de Alcalá, 12",
        "href": "https://pisos.com/comprar/piso-madrid_centro-12345678901_100500/",
        "site": "pisoscom",
    }


def test_parse_pisoscom_missing_fields_and_non_listings(load_fixture):
    listings = parse_fixture(load_fixture("pisoscom/search_results.html"))

    assert len(listings) == 2
    assert listings[1] == {
        "id": "99887766554",
        "price": "",
        "m2": "45 m²",
        "rooms": "",
        "floor": "Bajo",
        "town": "",
        "neighbour": "",
        "street": "",
        "number": "",
        "type": "buy",
        "title": "Ático en Retiro",
        "href": "https://pisos.com/comprar/atico-madrid_retiro-99887766554_100500/",
        "site": "pisoscom",
    }

