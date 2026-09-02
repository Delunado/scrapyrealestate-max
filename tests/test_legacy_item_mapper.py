from datetime import datetime, timezone

import pytest

from scrapyrealestate.domain.legacy_mapper import LegacyItemMappingError, map_legacy_item
from scrapyrealestate.domain.values import PortalKey, PropertyType, TransactionType
from scrapyrealestate.items import ScrapyrealestateItem


def test_maps_every_legacy_item_field_and_keeps_raw_diagnostics():
    observed_at = datetime(2026, 2, 3, 12, tzinfo=timezone.utc)
    raw = {
        "id": " 123 ",
        "price": "325.000 €",
        "m2": "92 m²",
        "rooms": "3 hab.",
        "floor": "2ª planta",
        "town": " Madrid ",
        "neighbour": "Sol",
        "street": "calle de Alcalá",
        "number": "12",
        "type": "buy",
        "title": "Piso en calle de Alcalá, 12",
        "href": "HTTPS://PISOS.COM/comprar/123#gallery",
        "site": "pisoscom",
        "post_time": "2026-02-02T10:00:00+01:00",
    }

    listing = map_legacy_item(raw, observed_at=observed_at)

    assert listing.portal is PortalKey.PISOSCOM
    assert listing.transaction_type is TransactionType.BUY
    assert listing.external_id == "123"
    assert listing.canonical_url == "https://pisos.com/comprar/123"
    assert listing.property_type is PropertyType.APARTMENT
    assert listing.price_euros == 325_000
    assert listing.area_sqm == 92
    assert listing.rooms == 3
    assert listing.floor == 2
    assert listing.location == "Madrid"
    assert listing.neighbourhood == "Sol"
    assert listing.street == "calle de Alcalá"
    assert listing.street_number == "12"
    assert listing.posted_at == datetime(2026, 2, 2, 9, tzinfo=timezone.utc)
    assert listing.observed_at is observed_at
    assert dict(listing.raw_source) == raw


@pytest.mark.parametrize("site", [key.value for key in PortalKey])
def test_maps_all_current_portal_keys(site):
    listing = map_legacy_item(
        {"site": site, "type": "alquiler", "title": "Casa", "id": 42}
    )

    assert listing.portal is PortalKey(site)
    assert listing.transaction_type is TransactionType.RENT


def test_accepts_the_scrapy_item_contract():
    item = ScrapyrealestateItem(
        id="7",
        title="Piso",
        type="venta",
        site="fotocasa",
        href="https://www.fotocasa.es/es/comprar/vivienda/7",
    )

    assert map_legacy_item(item).external_id == "7"


@pytest.mark.parametrize(
    "raw",
    [
        {"site": "pisoscom", "type": "buy", "title": "Piso"},
        {
            "site": "pisoscom",
            "type": "buy",
            "title": "Piso",
            "id": "",
            "href": "/relative",
        },
    ],
)
def test_rejects_records_without_usable_identity(raw):
    with pytest.raises(LegacyItemMappingError, match="no usable"):
        map_legacy_item(raw)


def test_invalid_optional_values_remain_in_raw_diagnostics():
    listing = map_legacy_item(
        {
            "site": "yaencontre",
            "type": "buy",
            "title": "Inmueble singular",
            "id": "1",
            "href": "javascript:void(0)",
            "price": "consultar",
            "post_time": "ayer",
        }
    )

    assert listing.canonical_url is None
    assert listing.price_euros is None
    assert listing.posted_at is None
    assert listing.raw_source["post_time"] == "ayer"
