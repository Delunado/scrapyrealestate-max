from scrapy.http import HtmlResponse, Request

from scrapyrealestate.spiders.pisoscom_spider import PisoscomSpider


def _response(url: str, html: str) -> HtmlResponse:
    request = Request(url)
    return HtmlResponse(url=url, request=request, body=html.encode(), encoding="utf-8")


def _card(listing_id: str, town: str = "Málaga Capital") -> str:
    return f"""
    <div class="ad-preview__info">
      <a class="ad-preview__title"
         href="/comprar/piso-malaga-{listing_id}_100500/">Piso en Málaga</a>
      <p class="p-sm">{town}</p>
      <span class="ad-preview__price">300.000 €</span>
      <p class="ad-preview__char p-sm">3 hab.</p>
      <p class="ad-preview__char p-sm">90 m²</p>
    </div>
    """


def test_restrictive_search_can_reach_a_valid_listing_beyond_page_one():
    first_url = "https://www.pisos.com/venta/pisos-malaga_capital_zona_urbana/"
    second_url = f"{first_url}2/"
    spider = PisoscomSpider(start_urls=first_url, page_limit=2, result_limit=10)
    first_outputs = list(
        spider.parse(
            _response(
                first_url,
                _card("11111111111", "Marbella")
                + f'<div class="pagination__next"><a href="{second_url}">Siguiente</a></div>',
            )
        )
    )

    next_request = first_outputs[-1]
    assert next_request.url == second_url

    second_outputs = list(next_request.callback(_response(second_url, _card("22222222222"))))
    assert [item["id"] for item in second_outputs] == ["22222222222"]


def test_page_and_result_limits_stop_pagination_cleanly():
    first_url = "https://www.pisos.com/venta/pisos-malaga_capital_zona_urbana/"
    second_url = f"{first_url}2/"
    spider = PisoscomSpider(start_urls=first_url, page_limit=5, result_limit=1)

    outputs = list(
        spider.parse(
            _response(
                first_url,
                _card("11111111111")
                + _card("22222222222")
                + f'<div class="pagination__next"><a href="{second_url}">Siguiente</a></div>',
            )
        )
    )

    assert len(outputs) == 1
    assert outputs[0]["id"] == "11111111111"


def test_empty_page_stops_even_if_a_stale_next_link_is_present():
    first_url = "https://www.pisos.com/venta/pisos-malaga_capital_zona_urbana/"
    outputs = list(
        PisoscomSpider(start_urls=first_url).parse(
            _response(
                first_url,
                '<div class="pagination__next"><a href="2/">Siguiente</a></div>',
            )
        )
    )

    assert outputs == []


def test_page_limit_prevents_following_another_nonempty_page():
    first_url = "https://www.pisos.com/venta/pisos-malaga_capital_zona_urbana/"
    outputs = list(
        PisoscomSpider(start_urls=first_url, page_limit=1).parse(
            _response(
                first_url,
                _card("11111111111")
                + '<div class="pagination__next"><a href="2/">Siguiente</a></div>',
            )
        )
    )

    assert len(outputs) == 1
    assert outputs[0]["id"] == "11111111111"
