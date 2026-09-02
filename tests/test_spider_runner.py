import sys
import time
from pathlib import Path

from scrapyrealestate.domain.values import PortalKey, RunStatus, TransactionType
from scrapyrealestate.execution.contract import PortalRunRequest
from scrapyrealestate.execution.runner import SpiderRunner, default_scrapy_command

FAKE_SPIDER = Path(__file__).parent / "fixtures" / "execution" / "fake_spider.py"


def _request(tmp_path: Path, *, timeout_seconds: float = 30) -> PortalRunRequest:
    return PortalRunRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url="https://www.pisos.com/venta/pisos-madrid/",
        transaction_type=TransactionType.BUY,
        output_path=tmp_path / "attempt.jl",
        timeout_seconds=timeout_seconds,
    )


def _fake_command(mode: str):
    def build(request: PortalRunRequest):
        return [sys.executable, str(FAKE_SPIDER), mode, str(request.output_path)]

    return build


def test_default_scrapy_command_matches_legacy_cli_conventions(tmp_path):
    request = _request(tmp_path)

    command = default_scrapy_command(request)

    assert command[:4] == ["scrapy", "crawl", "-L", "INFO"]
    assert command[4] == "pisoscom"
    assert command[5:7] == ["-o", f"{request.output_path}:jsonlines"]
    assert command[7:9] == ["-a", f"start_urls={request.start_url}"]


def test_success_returns_items_and_success_status(tmp_path):
    runner = SpiderRunner(working_directory=tmp_path, build_command=_fake_command("success"))

    result = runner.run(_request(tmp_path))

    assert result.status is RunStatus.SUCCESS
    assert result.items == ({"id": "1"}, {"id": "2"})
    assert result.return_code == 0


def test_no_results_is_empty_not_an_error(tmp_path):
    runner = SpiderRunner(working_directory=tmp_path, build_command=_fake_command("empty"))

    result = runner.run(_request(tmp_path))

    assert result.status is RunStatus.EMPTY
    assert result.items == ()
    assert result.return_code == 0


def test_nonzero_return_code_is_a_transport_error(tmp_path):
    runner = SpiderRunner(
        working_directory=tmp_path,
        build_command=_fake_command("fail"),
        max_diagnostic_bytes=50,
    )

    result = runner.run(_request(tmp_path))

    assert result.status is RunStatus.TRANSPORT_ERROR
    assert result.return_code == 1
    assert result.items == ()


def test_stderr_diagnostic_is_bounded(tmp_path):
    runner = SpiderRunner(
        working_directory=tmp_path,
        build_command=_fake_command("fail"),
        max_diagnostic_bytes=50,
    )

    result = runner.run(_request(tmp_path))

    assert result.diagnostic is not None
    assert result.diagnostic.endswith("... [truncated]")
    assert len(result.diagnostic.encode("utf-8")) <= 50 + len("\n... [truncated]")


def test_malformed_output_is_a_parser_error(tmp_path):
    runner = SpiderRunner(working_directory=tmp_path, build_command=_fake_command("malformed"))

    result = runner.run(_request(tmp_path))

    assert result.status is RunStatus.PARSER_ERROR
    assert result.return_code == 0
    assert result.items == ()
    assert "not valid JSON" in result.diagnostic


def test_missing_executable_is_a_transport_error(tmp_path):
    def build(request: PortalRunRequest):
        return ["definitely-not-a-real-scrapy-executable-xyz", "crawl"]

    runner = SpiderRunner(working_directory=tmp_path, build_command=build)

    result = runner.run(_request(tmp_path))

    assert result.status is RunStatus.TRANSPORT_ERROR
    assert "could not launch" in result.diagnostic


def test_timeout_is_recorded_and_the_child_is_killed(tmp_path):
    # The "hang" fake spider only writes its marker file if it is allowed to
    # sleep to completion (5s); a 0.5s timeout must kill it long before that.
    marker = tmp_path / "marker.txt"
    runner = SpiderRunner(working_directory=tmp_path, build_command=_fake_command("hang"))
    request = PortalRunRequest(
        portal=PortalKey.PISOSCOM,
        spider_name="pisoscom",
        start_url="https://www.pisos.com/venta/pisos-madrid/",
        transaction_type=TransactionType.BUY,
        output_path=marker,
        timeout_seconds=0.5,
    )

    started = time.monotonic()
    result = runner.run(request)
    elapsed = time.monotonic() - started

    assert result.status is RunStatus.TIMEOUT
    assert result.items == ()
    assert elapsed < 4  # well under the fake spider's 5s sleep
    time.sleep(1)  # give a leaked process a chance to finish, if it wasn't killed
    assert not marker.exists()
