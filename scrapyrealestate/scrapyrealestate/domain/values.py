"""Stable serialized values used at domain boundaries."""

from enum import StrEnum


class TransactionType(StrEnum):
    """The commercial operation represented by a search or listing."""

    BUY = "buy"
    RENT = "rent"


class PropertyType(StrEnum):
    """Portal-independent property categories."""

    APARTMENT = "apartment"
    HOUSE = "house"
    LAND = "land"
    COMMERCIAL = "commercial"
    OFFICE = "office"
    GARAGE = "garage"
    STORAGE = "storage"
    BUILDING = "building"
    OTHER = "other"
    UNKNOWN = "unknown"


class TriState(StrEnum):
    """A nullable fact that must not collapse unknown into false."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

    @classmethod
    def from_bool(cls, value: bool | None) -> "TriState":
        if value is None:
            return cls.UNKNOWN
        return cls.YES if value else cls.NO

    def to_bool(self) -> bool | None:
        if self is TriState.UNKNOWN:
            return None
        return self is TriState.YES


class PortalKey(StrEnum):
    """Stable keys for the currently supported property portals."""

    PISOSCOM = "pisoscom"
    HABITACLIA = "habitaclia"
    FOTOCASA = "fotocasa"
    YAENCONTRE = "yaencontre"
    IDEALISTA = "idealista"
    IDEALISTA_PROXY = "idealista_proxy"


class RunStatus(StrEnum):
    """Operational outcome of an isolated portal attempt."""

    SUCCESS = "success"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    PARSER_ERROR = "parser_error"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
