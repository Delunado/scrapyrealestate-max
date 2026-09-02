from datetime import datetime, timezone

import pytest

from scrapyrealestate.domain.capabilities import (
    CapabilityReport,
    FilterCapabilities,
    SearchFilterKey,
)
from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalRequest,
    PortalRequestError,
    PortalTransport,
    normalize_hostname,
)


def make_metadata(**overrides) -> PortalMetadata:
    values = {
        "key": PortalKey.PISOSCOM,
        "display_name": "Pisos.com",
        "domains": frozenset({"pisos.com"}),
        "spider_name": "pisoscom",
        "transaction_types": frozenset({TransactionType.BUY, TransactionType.RENT}),
        "transport": PortalTransport.HTTP,
        "capabilities": ALL_LOCAL_CAPABILITIES,
    }
    values.update(overrides)
    return PortalMetadata(**values)


class _FakeAdapter(BasePortalAdapter):
    _METADATA = make_metadata()

    def _transaction_type(self, raw_url: str) -> TransactionType | None:
        if "alquiler" in raw_url:
            return TransactionType.RENT
        if "venta" in raw_url:
            return TransactionType.BUY
        return None

    def _apply_recent_sort(self, raw_url: str) -> str:
        return f"{raw_url}?recent=1"


def test_normalize_hostname_lowercases_and_strips_www():
    assert normalize_hostname("WWW.Pisos.com") == "pisos.com"
    assert normalize_hostname("pisos.com") == "pisos.com"


def test_metadata_normalizes_domains_and_exposes_requires_browser():
    metadata = make_metadata(domains=frozenset({"WWW.Pisos.com"}))
    assert metadata.domains == frozenset({"pisos.com"})
    assert metadata.requires_browser is False

    browser_metadata = make_metadata(transport=PortalTransport.PLAYWRIGHT)
    assert browser_metadata.requires_browser is True
    assert browser_metadata.to_dict()["requires_browser"] is True


def test_metadata_rejects_empty_domains_and_transaction_types():
    with pytest.raises(ValueError, match="domains"):
        make_metadata(domains=frozenset())
    with pytest.raises(ValueError, match="transaction_types"):
        make_metadata(transaction_types=frozenset())


def test_metadata_requires_capabilities_to_be_a_filter_capabilities_instance():
    with pytest.raises(TypeError, match="FilterCapabilities"):
        make_metadata(capabilities={"remote": [], "local": [], "unsupported": []})


def test_base_adapter_builds_request_with_recent_sort_and_transaction_type():
    adapter = _FakeAdapter()
    request = adapter.build_request("https://www.pisos.com/venta/pisos-madrid/")

    assert request == PortalRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url="https://www.pisos.com/venta/pisos-madrid/?recent=1",
        transaction_type=TransactionType.BUY,
        raw_url="https://www.pisos.com/venta/pisos-madrid/",
    )


def test_base_adapter_rejects_wrong_domain():
    adapter = _FakeAdapter()
    with pytest.raises(PortalRequestError, match="does not belong"):
        adapter.build_request("https://www.otherportal.com/venta/pisos-madrid/")


def test_base_adapter_rejects_non_absolute_url():
    adapter = _FakeAdapter()
    with pytest.raises(PortalRequestError, match="not an absolute"):
        adapter.build_request("/venta/pisos-madrid/")


def test_base_adapter_rejects_unresolvable_transaction_type():
    adapter = _FakeAdapter()
    with pytest.raises(PortalRequestError, match="transaction type"):
        adapter.build_request("https://www.pisos.com/traspaso/pisos-madrid/")


def test_metadata_reports_capabilities_for_only_the_requested_filters():
    metadata = make_metadata(
        capabilities=FilterCapabilities(
            unsupported=frozenset({SearchFilterKey.GARAGE}),
            local=frozenset(SearchFilterKey) - {SearchFilterKey.GARAGE},
        )
    )
    filters = SearchFilters(min_price_euros=100_000, garage=True)

    report = metadata.report_capabilities(filters)

    assert report == CapabilityReport(
        local=frozenset({SearchFilterKey.MIN_PRICE_EUROS}),
        unsupported=frozenset({SearchFilterKey.GARAGE}),
    )


def test_base_adapter_build_request_from_search_defaults_to_not_implemented():
    adapter = _FakeAdapter()
    search = NormalizedSearch(
        name="Madrid",
        transaction_type=TransactionType.BUY,
        filters=SearchFilters(location="Madrid"),
    )
    with pytest.raises(PortalRequestError, match="not implemented"):
        adapter.build_request_from_search(search)


def test_base_adapter_build_request_from_search_requires_a_normalized_search():
    with pytest.raises(TypeError, match="NormalizedSearch"):
        _FakeAdapter().build_request_from_search("not a search")


def test_base_adapter_build_request_from_search_rejects_unsupported_transaction_type():
    metadata = make_metadata(transaction_types=frozenset({TransactionType.BUY}))

    class _BuyOnlyAdapter(_FakeAdapter):
        _METADATA = metadata

        def _build_search_url(self, transaction_type, location_slug) -> str:
            return f"https://www.pisos.com/{location_slug}/"

    search = NormalizedSearch(
        name="Rent search",
        transaction_type=TransactionType.RENT,
        filters=SearchFilters(location="Madrid"),
    )
    with pytest.raises(PortalRequestError, match="does not support transaction type"):
        _BuyOnlyAdapter().build_request_from_search(search)


def test_base_adapter_build_request_from_search_requires_a_location():
    class _LocationAwareAdapter(_FakeAdapter):
        def _build_search_url(self, transaction_type, location_slug) -> str:
            return f"https://www.pisos.com/{transaction_type.value}/{location_slug}/"

    search = NormalizedSearch(name="No location", transaction_type=TransactionType.BUY)
    with pytest.raises(PortalRequestError, match="location filter is required"):
        _LocationAwareAdapter().build_request_from_search(search)


def test_base_adapter_normalizes_result_with_its_own_portal_key():
    adapter = _FakeAdapter()
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item = {
        "id": "123",
        "title": "Piso en Sol",
        "price": "100.000 €",
        "type": "buy",
        "href": "https://pisos.com/comprar/piso-madrid-123/",
        "site": "pisoscom",
    }

    normalized = adapter.normalize_result(item)
    expected = map_legacy_item(item, portal=PortalKey.PISOSCOM, observed_at=observed_at)
    assert normalized.portal is expected.portal is PortalKey.PISOSCOM
    assert normalized.external_id == expected.external_id
    assert normalized.title == expected.title
    assert normalized.price_euros == expected.price_euros
