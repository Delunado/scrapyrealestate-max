"""Portal adapter interface and metadata contract.

An adapter is the boundary between a normalized search and one portal's
existing spider: it declares stable identity/capability metadata, validates
and transforms a legacy raw search URL into a crawl-ready request, and
normalizes the spider's raw item output. Portal parsing quirks stay in the
spider; adapters must not duplicate selector logic, only URL/domain rules
already expressed by the wrapped spider's own dispatch.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar
from urllib.parse import urlsplit

from scrapyrealestate.domain.capabilities import (
    CapabilityReport,
    FilterCapabilities,
    SearchFilterKey,
    report_capabilities,
)
from scrapyrealestate.domain.legacy_mapper import map_legacy_item
from scrapyrealestate.domain.listing import NormalizedListing
from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.portals.location import slugify_location


class PortalRequestError(ValueError):
    """A raw search URL cannot be used to build a request for this adapter."""


class PortalTransport(StrEnum):
    """The mechanism an adapter's spider uses to fetch a portal's page."""

    HTTP = "http"
    PLAYWRIGHT = "playwright"
    ROTATING_PROXY_HTTP = "rotating_proxy_http"


def normalize_hostname(hostname: str) -> str:
    """Lowercase a hostname and drop a leading ``www.`` label for matching."""
    text = hostname.strip().lower()
    if text.startswith("www."):
        text = text[4:]
    return text


# Every normalized filter can already be evaluated locally against a
# NormalizedListing (see domain.filtering). Adapters that do not yet encode
# any filter remotely should use this conservative default rather than
# invent per-portal capability data ahead of the corresponding request
# builder; nothing here may claim a filter is remote or unsupported without
# an adapter actually behaving that way.
ALL_LOCAL_CAPABILITIES = FilterCapabilities(local=frozenset(SearchFilterKey))


@dataclass(frozen=True, slots=True)
class PortalMetadata:
    """Stable identity and operational facts about one portal integration."""

    key: PortalKey
    display_name: str
    domains: frozenset[str]
    spider_name: str
    transaction_types: frozenset[TransactionType]
    transport: PortalTransport
    capabilities: FilterCapabilities
    caveats: str = ""
    degraded: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, PortalKey):
            raise TypeError("key must be a PortalKey")

        display_name = self.display_name.strip()
        if not display_name:
            raise ValueError("display_name is required")
        object.__setattr__(self, "display_name", display_name)

        domains = frozenset(normalize_hostname(domain) for domain in self.domains)
        if not domains or any(not domain for domain in domains):
            raise ValueError("domains must contain at least one non-empty hostname")
        object.__setattr__(self, "domains", domains)

        spider_name = self.spider_name.strip()
        if not spider_name:
            raise ValueError("spider_name is required")
        object.__setattr__(self, "spider_name", spider_name)

        transaction_types = frozenset(self.transaction_types)
        if not transaction_types or any(
            not isinstance(value, TransactionType) for value in transaction_types
        ):
            raise ValueError(
                "transaction_types must contain at least one TransactionType"
            )
        object.__setattr__(self, "transaction_types", transaction_types)

        if not isinstance(self.transport, PortalTransport):
            raise TypeError("transport must be a PortalTransport")
        if not isinstance(self.capabilities, FilterCapabilities):
            raise TypeError("capabilities must be FilterCapabilities")

        object.__setattr__(self, "caveats", self.caveats.strip())

    @property
    def requires_browser(self) -> bool:
        """Whether the spider needs Playwright/Chromium to fetch results."""
        return self.transport is PortalTransport.PLAYWRIGHT

    def report_capabilities(self, filters: SearchFilters) -> CapabilityReport:
        """Classify each filter ``filters`` requests as remote/local/unsupported.

        Only filters the search actually constrains are classified; a filter
        left unset is omitted rather than silently implied one way or the
        other. Callers (a search-creation form, an orchestration log) can use
        this to show exactly what will happen to a given search on this
        portal instead of the portal's full, request-independent capability
        set.
        """
        return report_capabilities(filters, self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.value,
            "display_name": self.display_name,
            "domains": sorted(self.domains),
            "spider_name": self.spider_name,
            "transaction_types": sorted(value.value for value in self.transaction_types),
            "transport": self.transport.value,
            "requires_browser": self.requires_browser,
            "capabilities": self.capabilities.to_dict(),
            "caveats": self.caveats,
            "degraded": self.degraded,
        }


@dataclass(frozen=True, slots=True)
class PortalRequest:
    """A validated, crawl-ready request built from a legacy raw search URL."""

    portal: PortalKey
    spider_name: str
    start_url: str
    transaction_type: TransactionType
    raw_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.portal, PortalKey):
            raise TypeError("portal must be a PortalKey")
        if not isinstance(self.transaction_type, TransactionType):
            raise TypeError("transaction_type must be a TransactionType")

        spider_name = self.spider_name.strip()
        if not spider_name:
            raise ValueError("spider_name is required")
        object.__setattr__(self, "spider_name", spider_name)

        for field_name in ("start_url", "raw_url"):
            value = getattr(self, field_name).strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)


class PortalAdapter(ABC):
    """Boundary between a normalized search and one portal's spider."""

    @property
    @abstractmethod
    def metadata(self) -> PortalMetadata:
        """Stable identity, domains, spider, transaction types, capabilities."""

    @abstractmethod
    def build_request(self, raw_url: str) -> PortalRequest:
        """Validate a legacy raw search URL and build a crawl-ready request."""

    @abstractmethod
    def normalize_result(self, item: Mapping[str, Any]) -> NormalizedListing:
        """Normalize one raw item emitted by this adapter's spider."""


class BasePortalAdapter(PortalAdapter):
    """Shared request-building/normalization for adapters wrapping a spider.

    Concrete adapters supply ``metadata`` plus the two portal-specific hooks
    below; domain/transaction validation and normalization stay identical
    across portals.
    """

    _METADATA: ClassVar[PortalMetadata]

    @property
    def metadata(self) -> PortalMetadata:
        return self._METADATA

    def build_request(self, raw_url: str) -> PortalRequest:
        metadata = self.metadata
        candidate = raw_url.strip()
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise PortalRequestError(
                f"{metadata.key.value}: not an absolute HTTP(S) URL: {raw_url!r}"
            )
        hostname = normalize_hostname(parsed.hostname)
        if hostname not in metadata.domains:
            raise PortalRequestError(
                f"{metadata.key.value}: URL host {parsed.hostname!r} does not belong "
                f"to {metadata.display_name}"
            )

        transaction_type = self._transaction_type(candidate)
        if transaction_type is None or transaction_type not in metadata.transaction_types:
            raise PortalRequestError(
                f"{metadata.key.value}: cannot determine a supported transaction type "
                f"from URL: {raw_url!r}"
            )

        return PortalRequest(
            portal=metadata.key,
            spider_name=metadata.spider_name,
            start_url=self._apply_recent_sort(candidate),
            transaction_type=transaction_type,
            raw_url=candidate,
        )

    def normalize_result(self, item: Mapping[str, Any]) -> NormalizedListing:
        return map_legacy_item(item, portal=self.metadata.key)

    def build_request_from_search(self, search: NormalizedSearch) -> PortalRequest:
        """Build a crawl-ready request directly from a normalized search.

        Unlike :meth:`build_request`, this needs no pre-existing legacy raw
        URL: it encodes the search's transaction type and location into a
        fresh search URL via :meth:`_build_search_url`. Only the location
        filter is encoded remotely (a best-effort municipality slug; see
        ``portals.location``); every other filter, including location
        itself once results come back, is still evaluated locally. Adapters
        that do not implement :meth:`_build_search_url` raise
        ``PortalRequestError`` explicitly rather than silently falling back
        to an unrelated URL.
        """
        if not isinstance(search, NormalizedSearch):
            raise TypeError("search must be a NormalizedSearch")
        metadata = self.metadata
        if search.transaction_type not in metadata.transaction_types:
            raise PortalRequestError(
                f"{metadata.key.value}: does not support transaction type "
                f"{search.transaction_type.value!r}"
            )

        location = (search.filters.location or "").strip()
        if not location:
            raise PortalRequestError(
                f"{metadata.key.value}: a location filter is required to build "
                "a search URL for this portal"
            )
        try:
            location_slug = slugify_location(location)
        except ValueError as error:
            raise PortalRequestError(f"{metadata.key.value}: {error}") from error

        search_url = self._build_search_url(search.transaction_type, location_slug)
        return PortalRequest(
            portal=metadata.key,
            spider_name=metadata.spider_name,
            start_url=self._apply_recent_sort(search_url),
            transaction_type=search.transaction_type,
            raw_url=search_url,
        )

    def _build_search_url(
        self, transaction_type: TransactionType, location_slug: str
    ) -> str:
        """Build this portal's search URL, before the recent-sort suffix.

        The default keeps :meth:`build_request_from_search` explicit about
        portals that do not (yet) support normalized-search URL
        construction; adapters that do override this hook instead.
        """
        raise PortalRequestError(
            f"{self.metadata.key.value}: normalized search URL construction "
            "is not implemented for this portal"
        )

    @abstractmethod
    def _transaction_type(self, raw_url: str) -> TransactionType | None:
        """Infer the transaction type this adapter's spider would derive."""

    @abstractmethod
    def _apply_recent_sort(self, raw_url: str) -> str:
        """Return the URL requesting the portal's most-recent-first order."""
