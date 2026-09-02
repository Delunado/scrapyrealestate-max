from datetime import datetime, timezone
from pathlib import Path

from scrapyrealestate.domain.search import NormalizedSearch, SearchFilters
from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.execution.attempt import run_portal_attempt
from scrapyrealestate.execution.contract import PortalRunResult
from scrapyrealestate.portals.base import (
    ALL_LOCAL_CAPABILITIES,
    PortalAdapter,
    PortalMetadata,
    PortalRequest,
    PortalRequestError,
    PortalTransport,
)


class _FakeAdapter(PortalAdapter):
    def __init__(self, *, on_build_from_search=None, on_build=None):
        self._metadata = PortalMetadata(
            key=PortalKey.PISOSCOM,
            display_name="Fake",
            domains=frozenset({"fake.test"}),
            spider_name="fake",
            transaction_types=frozenset({TransactionType.BUY}),
            transport=PortalTransport.HTTP,
            capabilities=ALL_LOCAL_CAPABILITIES,
        )
        self._on_build_from_search = on_build_from_search
        self._on_build = on_build
        self.build_from_search_calls = 0
        self.build_calls = 0

    @property
    def metadata(self):
        return self._metadata

    def build_request(self, raw_url):
        self.build_calls += 1
        if self._on_build is None:
            raise PortalRequestError("build_request not configured")
        return self._on_build(raw_url)

    def normalize_result(self, item):
        raise NotImplementedError

    def build_request_from_search(self, search):
        self.build_from_search_calls += 1
        if self._on_build_from_search is None:
            raise PortalRequestError("build_request_from_search not configured")
        return self._on_build_from_search(search)


class _FakeRunner:
    def __init__(self, *, result=None, error=None):
        self._result = result
        self._error = error
        self.received_request = None

    def run(self, request):
        self.received_request = request
        if self._error is not None:
            raise self._error
        return self._result


def _search() -> NormalizedSearch:
    return NormalizedSearch(
        name="Madrid flats",
        transaction_type=TransactionType.BUY,
        filters=SearchFilters(location="Madrid"),
    )


def _portal_request() -> PortalRequest:
    return PortalRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="fake",
        start_url="https://fake.test/venta/pisos-madrid/",
        transaction_type=TransactionType.BUY,
        raw_url="https://fake.test/venta/pisos-madrid/",
    )


def _success_result() -> PortalRunResult:
    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    return PortalRunResult(
        portal=PortalKey.PISOSCOM,
        status=RunStatus.SUCCESS,
        started_at=now,
        finished_at=now,
        items=({"id": "1"},),
    )


def test_request_build_failure_is_recorded_as_unavailable_without_raising(tmp_path: Path):
    adapter = _FakeAdapter(
        on_build_from_search=lambda search: (_ for _ in ()).throw(
            PortalRequestError("no location taxonomy")
        )
    )
    runner = _FakeRunner(result=_success_result())

    result = run_portal_attempt(
        adapter, _search(), runner=runner, output_path=tmp_path / "out.jl"
    )

    assert result.status is RunStatus.UNAVAILABLE
    assert "no location taxonomy" in result.diagnostic
    assert runner.received_request is None


def test_unexpected_adapter_error_is_recorded_as_unavailable_without_raising(tmp_path: Path):
    def _boom(search):
        raise RuntimeError("adapter bug")

    adapter = _FakeAdapter(on_build_from_search=_boom)
    runner = _FakeRunner(result=_success_result())

    result = run_portal_attempt(
        adapter, _search(), runner=runner, output_path=tmp_path / "out.jl"
    )

    assert result.status is RunStatus.UNAVAILABLE
    assert "unexpected error building request" in result.diagnostic
    assert "adapter bug" in result.diagnostic


def test_unexpected_runner_error_is_recorded_as_transport_error_without_raising(tmp_path: Path):
    adapter = _FakeAdapter(on_build_from_search=lambda search: _portal_request())
    runner = _FakeRunner(error=RuntimeError("subprocess module exploded"))

    result = run_portal_attempt(
        adapter, _search(), runner=runner, output_path=tmp_path / "out.jl"
    )

    assert result.status is RunStatus.TRANSPORT_ERROR
    assert "unexpected runner failure" in result.diagnostic
    assert "subprocess module exploded" in result.diagnostic


def test_successful_attempt_passes_through_the_runner_result(tmp_path: Path):
    expected = _success_result()
    adapter = _FakeAdapter(on_build_from_search=lambda search: _portal_request())
    runner = _FakeRunner(result=expected)
    output_path = tmp_path / "out.jl"

    result = run_portal_attempt(
        adapter,
        _search(),
        runner=runner,
        output_path=output_path,
        timeout_seconds=45,
        log_level="DEBUG",
    )

    assert result is expected
    assert runner.received_request.output_path == output_path
    assert runner.received_request.timeout_seconds == 45
    assert runner.received_request.log_level == "DEBUG"
    assert runner.received_request.portal is PortalKey.PISOSCOM


def test_raw_url_override_uses_build_request_instead_of_search(tmp_path: Path):
    adapter = _FakeAdapter(
        on_build=lambda raw_url: _portal_request(),
        on_build_from_search=lambda search: (_ for _ in ()).throw(
            AssertionError("should not build from search when an override is given")
        ),
    )
    runner = _FakeRunner(result=_success_result())

    run_portal_attempt(
        adapter,
        _search(),
        runner=runner,
        output_path=tmp_path / "out.jl",
        raw_url_override="https://fake.test/venta/pisos-madrid/",
    )

    assert adapter.build_calls == 1
    assert adapter.build_from_search_calls == 0
