"""Lookup of portal adapters by stable key or hostname."""

from collections.abc import Iterable, Iterator

from scrapyrealestate.domain.values import PortalKey
from scrapyrealestate.portals.base import PortalAdapter, normalize_hostname


class PortalRegistrationError(ValueError):
    """A portal adapter conflicts with one already registered."""


class PortalRegistry:
    """A collection of adapters keyed by stable portal key and hostname.

    Registration rejects a duplicate portal key or a domain already claimed by
    another adapter so two portals can never shadow each other.
    """

    def __init__(self, adapters: Iterable[PortalAdapter] = ()) -> None:
        self._by_key: dict[PortalKey, PortalAdapter] = {}
        self._by_domain: dict[str, PortalAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: PortalAdapter) -> None:
        metadata = adapter.metadata
        if metadata.key in self._by_key:
            raise PortalRegistrationError(
                f"portal key already registered: {metadata.key.value}"
            )
        conflicting = sorted(metadata.domains & self._by_domain.keys())
        if conflicting:
            raise PortalRegistrationError(
                f"domain already registered: {', '.join(conflicting)}"
            )

        self._by_key[metadata.key] = adapter
        for domain in metadata.domains:
            self._by_domain[domain] = adapter

    def get(self, key: PortalKey) -> PortalAdapter:
        try:
            return self._by_key[key]
        except KeyError as error:
            name = key.value if isinstance(key, PortalKey) else key
            raise KeyError(f"unknown portal key: {name!r}") from error

    def get_by_hostname(self, hostname: str) -> PortalAdapter:
        normalized = normalize_hostname(hostname)
        adapter = self._by_domain.get(normalized)
        if adapter is None:
            raise KeyError(f"no portal registered for hostname: {hostname!r}")
        return adapter

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[PortalAdapter]:
        return iter(self._by_key.values())

    def __contains__(self, key: object) -> bool:
        return key in self._by_key
