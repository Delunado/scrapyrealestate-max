from scrapy.http import HtmlResponse, Request

from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.spiders.habitaclia_spider import (
    HabitacliaSpider,
    habitaclia_listing_id,
)


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
        "id": "123456789",
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


def test_every_habitaclia_result_satisfies_normalized_boundary(load_fixture):
    normalized = [
        map_legacy_item(item)
        for item in parse_fixture(load_fixture("habitaclia/search_results.html"))
    ]

    assert len(normalized) == 1
    listing = normalized[0]
    assert listing.portal is PortalKey.HABITACLIA
    assert listing.transaction_type is TransactionType.RENT
    assert listing.price_euros == 1_250
    assert listing.area_sqm == 85
    assert listing.rooms == 3
    assert listing.canonical_url == (
        "https://www.habitaclia.com/"
        "alquiler-piso-calle_de_alcala_12-madrid-i123456789.htm"
    )


def test_habitaclia_identity_prefers_url_id_and_ignores_mutable_query_values():
    assert habitaclia_listing_id(
        "https://www.habitaclia.com/alquiler-piso-madrid-i987654321.htm"
    ) == "987654321"

    fallback_url = "https://www.habitaclia.com/alquiler-piso-singular-madrid.htm"
    first = habitaclia_listing_id(f"{fallback_url}?precio=1250&campaign=one")
    changed = habitaclia_listing_id(f"{fallback_url}?precio=1400&campaign=two")

    assert first == changed
    assert first.isdecimal()
    assert 0 < int(first) < 2**63
