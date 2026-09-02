from scrapy.http import HtmlResponse, Request

from scrapyrealestate.spiders.habitaclia_spider import HabitacliaSpider


SEARCH_URL = "https://www.habitaclia.com/alquiler-madrid.htm?ordenar=mas_recientes"


def parse_fixture(html: str) -> list[dict]:
    request = Request(SEARCH_URL)
    response = HtmlResponse(
        url=SEARCH_URL,
        request=request,
        body=html.encode(),
        encoding="utf-8",
    )
    spider = HabitacliaSpider(start_urls=SEARCH_URL)
    return [dict(item) for item in spider.parse(response)]


def test_parse_habitaclia_result_fields(load_fixture):
    listings = parse_fixture(load_fixture("habitaclia/search_results.html"))

    assert listings[0] == {
        "id": "3125085",
        "price": "1.250€/mes",
        "m2": "85 m",
        "rooms": "3 hab",
        "floor": "",
        "town": "Madrid",
        "neighbour": "Goya",
        "street": "Calle de Alcalá 12",
        "number": "12",
        "type": "rent",
        "title": "Alquiler Piso  en  Calle de Alcalá 12. Vivienda exterior reformada",
        "href": (
            "https://www.habitaclia.com/"
            "alquiler-piso-calle_de_alcala_12-madrid-i123456789.htm"
        ),
        "site": "habitaclia",
    }


def test_parse_habitaclia_stops_at_related_ad(load_fixture):
    listings = parse_fixture(load_fixture("habitaclia/search_results.html"))

    assert len(listings) == 1
