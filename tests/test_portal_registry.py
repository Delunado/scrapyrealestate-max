import pytest

from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalMetadata,
    PortalTransport,
)
from scrapyrealestate.portals.registry import PortalRegistrationError, PortalRegistry


def make_adapter(key: PortalKey, domain: str) -> BasePortalAdapter:
    class _Adapter(BasePortalAdapter):
        _METADATA = PortalMetadata(
            key=key,
            display_name=key.value,
            domains=frozenset({domain}),
            spider_name=key.value,
            transaction_types=frozenset({TransactionType.BUY}),
            transport=PortalTransport.HTTP,
            capabilities=ALL_LOCAL_CAPABILITIES,
        )

        def _transaction_type(self, raw_url: str) -> TransactionType | None:
            return TransactionType.BUY

        def _apply_recent_sort(self, raw_url: str) -> str:
            return raw_url

    return _Adapter()


def test_registry_looks_up_by_key_and_normalized_hostname():
    pisoscom = make_adapter(PortalKey.PISOSCOM, "pisos.com")
    habitaclia = make_adapter(PortalKey.HABITACLIA, "habitaclia.com")
    registry = PortalRegistry([pisoscom, habitaclia])

    assert len(registry) == 2
    assert registry.get(PortalKey.PISOSCOM) is pisoscom
    assert registry.get_by_hostname("www.pisos.com") is pisoscom
    assert registry.get_by_hostname("PISOS.COM") is pisoscom
    assert registry.get_by_hostname("habitaclia.com") is habitaclia
    assert PortalKey.PISOSCOM in registry
    assert {adapter.metadata.key for adapter in registry} == {
        PortalKey.PISOSCOM,
        PortalKey.HABITACLIA,
    }


def test_registry_rejects_duplicate_portal_key():
    registry = PortalRegistry([make_adapter(PortalKey.PISOSCOM, "pisos.com")])
    with pytest.raises(PortalRegistrationError, match="portal key already registered"):
        registry.register(make_adapter(PortalKey.PISOSCOM, "otherpisos.com"))


def test_registry_rejects_duplicate_domain():
    registry = PortalRegistry([make_adapter(PortalKey.PISOSCOM, "pisos.com")])
    with pytest.raises(PortalRegistrationError, match="domain already registered"):
        registry.register(make_adapter(PortalKey.HABITACLIA, "pisos.com"))


def test_registry_unknown_key_and_hostname_raise_key_error():
    registry = PortalRegistry([make_adapter(PortalKey.PISOSCOM, "pisos.com")])
    with pytest.raises(KeyError, match="unknown portal key"):
        registry.get(PortalKey.HABITACLIA)
    with pytest.raises(KeyError, match="no portal registered"):
        registry.get_by_hostname("idealista.com")
