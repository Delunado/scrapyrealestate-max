from datetime import datetime, timedelta, timezone

import pytest

from scrapyrealestate.domain.listing import NormalizedListing, canonicalize_url
from scrapyrealestate.domain.values import PortalKey, TransactionType


def test_listing_normalizes_identity_text_url_and_utc_timestamps():
    listing = NormalizedListing(
        portal=PortalKey.PISOSCOM,
        transaction_type=TransactionType.BUY,
        title="  Piso luminoso  ",
        external_id=" 123 ",
        canonical_url="HTTPS://PISOS.COM:443/anuncio?id=1#photos",
        location=" Madrid ",
        posted_at=datetime(2026, 1, 2, 10, tzinfo=timezone(timedelta(hours=1))),
        observed_at=datetime(2026, 1, 2, 10, tzinfo=timezone(timedelta(hours=1))),
        raw_source={"price": "325.000 €"},
    )

    assert listing.identity == (PortalKey.PISOSCOM, "123")
    assert listing.title == "Piso luminoso"
    assert listing.canonical_url == "https://pisos.com/anuncio?id=1"
    assert listing.location == "Madrid"
    assert listing.observed_at == datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    assert listing.to_dict()["posted_at"] == "2026-01-02T09:00:00Z"
    assert listing.to_dict()["raw_source"] == {"price": "325.000 €"}


def test_listing_uses_canonical_url_as_fallback_identity():
    listing = NormalizedListing(
        portal=PortalKey.HABITACLIA,
        transaction_type=TransactionType.RENT,
        title="Piso",
        canonical_url="https://habitaclia.com/alquiler-piso-1",
    )

    assert listing.identity == (
        PortalKey.HABITACLIA,
        "https://habitaclia.com/alquiler-piso-1",
    )


@pytest.mark.parametrize(
    "url",
    ["", "/relative", "ftp://pisos.com/1", "https://user:secret@pisos.com/1"],
)
def test_canonical_url_rejects_unsafe_or_non_absolute_values(url):
    with pytest.raises(ValueError):
        canonicalize_url(url)


def test_listing_rejects_missing_identity_and_naive_timestamps():
    with pytest.raises(ValueError, match="external_id or canonical_url"):
        NormalizedListing(
            portal=PortalKey.FOTOCASA,
            transaction_type=TransactionType.BUY,
            title="Piso",
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        NormalizedListing(
            portal=PortalKey.FOTOCASA,
            transaction_type=TransactionType.BUY,
            title="Piso",
            external_id="1",
            observed_at=datetime(2026, 1, 2),
        )


def test_listing_calculates_price_per_square_metre():
    listing = NormalizedListing(
        portal=PortalKey.YAENCONTRE,
        transaction_type=TransactionType.BUY,
        title="Piso",
        external_id="1",
        price_euros=300_000,
        area_sqm=100,
    )

    assert listing.price_per_sqm == 3000
