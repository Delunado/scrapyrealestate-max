import json

import pytest

from scrapyrealestate.domain.values import (
    PortalKey,
    PropertyType,
    RunStatus,
    TransactionType,
    TriState,
)


@pytest.mark.parametrize(
    ("enum_type", "member", "serialized"),
    [
        (TransactionType, TransactionType.BUY, "buy"),
        (PropertyType, PropertyType.APARTMENT, "apartment"),
        (TriState, TriState.UNKNOWN, "unknown"),
        (PortalKey, PortalKey.PISOSCOM, "pisoscom"),
        (RunStatus, RunStatus.TRANSPORT_ERROR, "transport_error"),
    ],
)
def test_domain_values_have_stable_json_serialization(enum_type, member, serialized):
    assert json.loads(json.dumps(member)) == serialized
    assert enum_type(serialized) is member


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, TriState.YES), (False, TriState.NO), (None, TriState.UNKNOWN)],
)
def test_tri_state_preserves_unknown(value, expected):
    state = TriState.from_bool(value)

    assert state is expected
    assert state.to_bool() is value


def test_invalid_serialized_value_is_rejected():
    with pytest.raises(ValueError):
        PortalKey("pisos.com")
