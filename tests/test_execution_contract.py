from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.execution.contract import (
    CONCLUSIVE_STATUSES,
    DEFAULT_LOG_LEVEL,
    DEFAULT_TIMEOUT_SECONDS,
    PortalRunRequest,
    PortalRunResult,
)
from scrapyrealestate.portals.base import PortalRequest


def _time(seconds: int = 0) -> datetime:
    return datetime(2026, 9, 2, 10, 0, seconds, tzinfo=timezone.utc)


def test_run_status_has_exactly_the_expected_operational_categories():
    # Locks down the seven categories orchestration and persistence
    # (RunStatus, persistence.runs) are both built around.
    assert {status.value for status in RunStatus} == {
        "success",
        "empty",
        "timeout",
        "transport_error",
        "parser_error",
        "blocked",
        "unavailable",
    }


def test_conclusive_statuses_are_only_success_and_empty():
    assert CONCLUSIVE_STATUSES == {RunStatus.SUCCESS, RunStatus.EMPTY}


def test_portal_run_request_requires_typed_portal_and_transaction():
    with pytest.raises(TypeError):
        PortalRunRequest(
            portal="pisoscom",
            spider_name="pisoscom",
            start_url="https://www.pisos.com/venta/pisos-madrid/",
            transaction_type=TransactionType.BUY,
            output_path=Path("out.jl"),
        )
    with pytest.raises(TypeError):
        PortalRunRequest(
            portal=PortalKey.PISOSCOM,
            spider_name="pisoscom",
            start_url="https://www.pisos.com/venta/pisos-madrid/",
            transaction_type="buy",
            output_path=Path("out.jl"),
        )


def test_portal_run_request_requires_non_blank_spider_name_and_url():
    with pytest.raises(ValueError, match="spider_name"):
        PortalRunRequest(
            portal=PortalKey.PISOSCOM,
            spider_name="  ",
            start_url="https://www.pisos.com/venta/pisos-madrid/",
            transaction_type=TransactionType.BUY,
            output_path=Path("out.jl"),
        )
    with pytest.raises(ValueError, match="start_url"):
        PortalRunRequest(
            portal=PortalKey.PISOSCOM,
            spider_name="pisoscom",
            start_url="   ",
            transaction_type=TransactionType.BUY,
            output_path=Path("out.jl"),
        )


def test_portal_run_request_rejects_non_positive_timeout():
    with pytest.raises(ValueError, match="timeout_seconds"):
        PortalRunRequest(
            portal=PortalKey.PISOSCOM,
            spider_name="pisoscom",
            start_url="https://www.pisos.com/venta/pisos-madrid/",
            transaction_type=TransactionType.BUY,
            output_path=Path("out.jl"),
            timeout_seconds=0,
        )


def test_portal_run_request_coerces_output_path_and_defaults_log_level():
    request = PortalRunRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url="https://www.pisos.com/venta/pisos-madrid/",
        transaction_type=TransactionType.BUY,
        output_path="relative/out.jl",
        log_level="  ",
    )

    assert request.output_path == Path("relative/out.jl")
    assert request.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert request.log_level == DEFAULT_LOG_LEVEL


def test_portal_run_request_from_portal_request_carries_adapter_fields():
    adapter_request = PortalRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url="https://www.pisos.com/venta/pisos-madrid/fecharecientedesde-desc/",
        transaction_type=TransactionType.BUY,
        raw_url="https://www.pisos.com/venta/pisos-madrid/",
    )

    request = PortalRunRequest.from_portal_request(
        adapter_request, output_path=Path("/tmp/attempt.jl"), timeout_seconds=30
    )

    assert request.portal is PortalKey.PISOSCOM
    assert request.spider_name == "pisoscom"
    assert request.start_url == adapter_request.start_url
    assert request.transaction_type is TransactionType.BUY
    assert request.output_path == Path("/tmp/attempt.jl")
    assert request.timeout_seconds == 30


def test_portal_run_request_to_dict():
    request = PortalRunRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url="https://www.pisos.com/venta/pisos-madrid/",
        transaction_type=TransactionType.BUY,
        output_path=Path("out.jl"),
    )

    assert request.to_dict() == {
        "portal": "pisoscom",
        "spider_name": "pisoscom",
        "start_url": "https://www.pisos.com/venta/pisos-madrid/",
        "transaction_type": "buy",
        "output_path": "out.jl",
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "log_level": DEFAULT_LOG_LEVEL,
    }


def test_portal_run_result_requires_timezone_aware_timestamps():
    with pytest.raises(ValueError, match="started_at"):
        PortalRunResult(
            portal=PortalKey.PISOSCOM,
            status=RunStatus.EMPTY,
            started_at=datetime(2026, 9, 2, 10, 0, 0),
            finished_at=_time(1),
        )


def test_portal_run_result_rejects_finished_before_started():
    with pytest.raises(ValueError, match="finished_at"):
        PortalRunResult(
            portal=PortalKey.PISOSCOM,
            status=RunStatus.EMPTY,
            started_at=_time(1),
            finished_at=_time(0),
        )


def test_portal_run_result_normalizes_timestamps_to_utc_and_computes_duration():
    madrid = timezone(timedelta(hours=2))
    result = PortalRunResult(
        portal=PortalKey.PISOSCOM,
        status=RunStatus.EMPTY,
        started_at=_time(0).astimezone(madrid),
        finished_at=_time(30).astimezone(madrid),
    )

    assert result.started_at.utcoffset() == timedelta(0)
    assert result.duration_seconds == 30


def test_portal_run_result_rejects_items_on_a_non_conclusive_status():
    with pytest.raises(ValueError, match="timeout"):
        PortalRunResult(
            portal=PortalKey.PISOSCOM,
            status=RunStatus.TIMEOUT,
            started_at=_time(0),
            finished_at=_time(1),
            items=({"id": "1"},),
        )


@pytest.mark.parametrize("status", sorted(CONCLUSIVE_STATUSES, key=lambda s: s.value))
def test_portal_run_result_conclusive_property(status):
    result = PortalRunResult(
        portal=PortalKey.PISOSCOM, status=status, started_at=_time(0), finished_at=_time(1)
    )
    assert result.conclusive is True


@pytest.mark.parametrize(
    "status",
    sorted(set(RunStatus) - CONCLUSIVE_STATUSES, key=lambda s: s.value),
)
def test_portal_run_result_not_conclusive_for_failure_statuses(status):
    result = PortalRunResult(
        portal=PortalKey.PISOSCOM, status=status, started_at=_time(0), finished_at=_time(1)
    )
    assert result.conclusive is False


def test_portal_run_result_to_dict():
    result = PortalRunResult(
        portal=PortalKey.PISOSCOM,
        status=RunStatus.SUCCESS,
        started_at=_time(0),
        finished_at=_time(2),
        items=({"id": "1"}, {"id": "2"}),
        return_code=0,
        diagnostic=None,
    )

    assert result.to_dict() == {
        "portal": "pisoscom",
        "status": "success",
        "started_at": "2026-09-02T10:00:00Z",
        "finished_at": "2026-09-02T10:00:02Z",
        "duration_seconds": 2.0,
        "item_count": 2,
        "return_code": 0,
        "diagnostic": None,
    }
