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
from scrapyrealestate.portals.fotocasa import FotocasaAdapter
from scrapyrealestate.portals.habitaclia import HabitacliaAdapter
from scrapyrealestate.portals.idealista import IdealistaAdapter, IdealistaProxyAdapter
from scrapyrealestate.portals.pisoscom import PisoscomAdapter
from scrapyrealestate.portals.registry import PortalRegistrationError, PortalRegistry
from scrapyrealestate.portals.yaencontre import YaencontreAdapter

__all__ = [
    "ALL_LOCAL_CAPABILITIES",
    "BasePortalAdapter",
    "FotocasaAdapter",
    "HabitacliaAdapter",
    "IdealistaAdapter",
    "IdealistaProxyAdapter",
    "PisoscomAdapter",
    "PortalAdapter",
    "PortalMetadata",
    "PortalRegistrationError",
    "PortalRegistry",
    "PortalRequest",
    "PortalRequestError",
    "PortalTransport",
    "YaencontreAdapter",
    "build_default_registry",
    "normalize_hostname",
]


def build_default_registry(*, idealista_proxy: bool = False) -> PortalRegistry:
    """Build the registry of adapters for every currently supported portal.

    Idealista's two adapters intentionally share the ``idealista.com``
    domain (see ``IdealistaProxyAdapter``), so only one of them can be
    registered at a time; ``idealista_proxy`` selects between them the same
    way legacy configuration's ``proxy_idealista`` flag does today, rather
    than by hostname routing.
    """
    idealista: PortalAdapter = (
        IdealistaProxyAdapter() if idealista_proxy else IdealistaAdapter()
    )
    return PortalRegistry(
        [
            PisoscomAdapter(),
            HabitacliaAdapter(),
            FotocasaAdapter(),
            YaencontreAdapter(),
            idealista,
        ]
    )
