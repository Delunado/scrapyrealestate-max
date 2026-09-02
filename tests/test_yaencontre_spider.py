from scrapy.http import HtmlResponse, Request

from scrapyrealestate.spiders.yaencontre_spider import YaencontreSpider


SEARCH_URL = "https://www.yaencontre.com/alquiler/pisos/madrid/o-recientes"


def parse_fixture(html: str) -> list[dict]:
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL,
        request=request,
        body=html.encode(),
        encoding="utf-8",
    )
    spider = YaencontreSpider(start_urls=SEARCH_URL)
    return [dict(item) for item in spider.parse(response)]


def test_parse_yaencontre_result_fields(load_fixture):
    listings = parse_fixture(load_fixture("yaencontre/search_results.html"))

    assert listings[0] == {
        "id": "24681012",
        "title": "Piso en calle Huesca, Castillejos, Madrid",
        "price": "1.400 €/mes",
        "rooms": "3 hab.",
        "m2": "88 m²",
        "floor": "",
        "town": "Madrid",
        "neighbour": "Castillejos",
        "street": "calle Huesca",
        "number": "",
        "type": "rent",
        "href": (
            "https://www.yaencontre.com/"
            "alquiler/piso-24681012-calle-huesca-madrid"
        ),
        "site": "yaencontre",
    }


def test_parse_yaencontre_missing_fields_and_non_listings(load_fixture):
    listings = parse_fixture(load_fixture("yaencontre/search_results.html"))

    assert len(listings) == 2
    assert listings[1] == {
        "id": "97531",
        "title": "Estudio en Lavapiés, Madrid",
        "price": "",
        "rooms": "",
        "m2": "",
        "floor": "",
        "town": "Madrid",
        "neighbour": "Lavapiés",
        "street": "",
        "number": "",
        "type": "rent",
        "href": (
            "https://www.yaencontre.com/"
            "alquiler/estudio-97531-lavapies-madrid"
        ),
        "site": "yaencontre",
    }
