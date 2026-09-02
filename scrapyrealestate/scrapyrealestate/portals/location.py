"""Conservative slugification of a free-text location for portal search URLs.

Portals encode a search location as a hyphenated, accent-free path segment
(``https://www.pisos.com/venta/pisos-madrid/``). For a plain Spanish
municipality name this transformation is deterministic and well tested;
provincial capitals, neighbourhoods, and multi-word disambiguated names
(``"Madrid, provincia"``) sometimes need a portal-specific taxonomy code
this module does not attempt to guess. Callers that need those should keep
using the raw-URL override rather than trusting this best-effort slug.
"""

import re
import unicodedata

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def slugify_location(location: str) -> str:
    """Return ``location`` as a lowercase, accent-free, hyphenated slug.

    Raises ``ValueError`` if nothing alphanumeric remains, so callers can
    surface a clear "no usable location" error instead of building a URL
    with an empty or malformed segment.
    """
    decomposed = unicodedata.normalize("NFKD", location)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    slug = _NON_ALPHANUMERIC.sub("-", unaccented.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"location has no usable text to slugify: {location!r}")
    return slug
