"""Portal adapter metadata, registry, and per-portal request/normalization."""

from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    BasePortalAdapter,
    PortalAdapter,
    PortalMetadata,
    PortalRequest,
    PortalRequestError,
    PortalTransport,
    normalize_hostname,
)

__all__ = [
    "ALL_LOCAL_CAPABILITIES",
    "BasePortalAdapter",
    "PortalAdapter",
    "PortalMetadata",
    "PortalRequest",
    "PortalRequestError",
    "PortalTransport",
    "normalize_hostname",
]
