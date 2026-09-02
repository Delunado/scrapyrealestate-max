from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.values import PortalKey, TransactionType
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


def test_every_yaencontre_result_satisfies_normalized_boundary(load_fixture):
    normalized = [
        map_legacy_item(item)
        for item in parse_fixture(load_fixture("yaencontre/search_results.html"))
    ]

    assert all(listing.portal is PortalKey.YAENCONTRE for listing in normalized)
    assert all(listing.transaction_type is TransactionType.RENT for listing in normalized)
    assert normalized[0].external_id == "24681012"
    assert normalized[0].price_euros == 1_400
    assert normalized[0].area_sqm == 88
    assert normalized[0].rooms == 3
    assert normalized[1].price_euros is None
    assert normalized[1].area_sqm is None
