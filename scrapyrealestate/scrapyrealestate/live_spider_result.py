"""Classify and summarize an opt-in live spider crawl."""

import argparse
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ResultKind(StrEnum):
    SUCCESS_NON_EMPTY = "SUCCESS_NON_EMPTY"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    PARSER_FAILURE = "PARSER_FAILURE"
    TRANSPORT_FAILURE = "TRANSPORT_FAILURE"
    LIKELY_BLOCKING = "LIKELY_BLOCKING"


class FailureClass(StrEnum):
    APPLICATION = "application"
    PARSER = "parser"
    SITE_CHANGE = "site_change"
    BLOCKING = "blocking"
    TRANSPORT = "transport"


@dataclass(frozen=True)
class CrawlResult:
    kind: ResultKind
    item_count: int = 0
    detail: str = ""
    failure_class: FailureClass | None = None


BLOCKING_MARKERS = (
    "datadome",
    "captcha",
    "access denied",
    "response_status_count/403",
    "response_status_count/429",
    "status code 403",
    "status code 429",
    "posible bloqueo anti-bot",
)

PARSER_FAILURE_MARKERS = (
    "spider_exceptions/",
    "spider error processing",
    "traceback (most recent call last)",
)

TRANSPORT_FAILURE_MARKERS = (
    "downloader/exception_type_count/",
    "dnslookup",
    "connectionrefused",
    "tcptimeout",
    "download timeout",
    "error al obtener datos de",
    "response_status_count/500",
    "response_status_count/502",
    "response_status_count/503",
    "response_status_count/504",
)

APPLICATION_FAILURE_MARKERS = (
    "modulenotfounderror",
    "no module named",
    "command not found",
    "is not recognized as an internal or external command",
    "reactoralreadyinstallederror",
    "permission denied",
    "operation not permitted",
    "browser executable doesn't exist",
    "object has no attribute 'browser_type'",
    "invalidstateerror",
)

SITE_CHANGE_MARKERS = (
    "no se encontrÃ³ el json __initial_props__",
    "wait_for_selector",
    "waiting for locator",
    "selector did not match",
)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def classify_crawl(output_path: Path, log_text: str, crawl_exit_code: int) -> CrawlResult:
    """Classify a crawl using its exit code, Scrapy log, and JSON feed."""
    normalized_log = log_text.lower()

    if _contains_any(normalized_log, APPLICATION_FAILURE_MARKERS):
        return CrawlResult(
            ResultKind.TRANSPORT_FAILURE,
            detail="the local crawl runtime could not start",
            failure_class=FailureClass.APPLICATION,
        )
    if _contains_any(normalized_log, BLOCKING_MARKERS):
        return CrawlResult(
            ResultKind.LIKELY_BLOCKING,
            detail="the crawl log contains an anti-bot or HTTP throttling marker",
            failure_class=FailureClass.BLOCKING,
        )
    if _contains_any(normalized_log, SITE_CHANGE_MARKERS):
        return CrawlResult(
            ResultKind.PARSER_FAILURE,
            detail="the expected portal response structure was not found",
            failure_class=FailureClass.SITE_CHANGE,
        )
    if _contains_any(normalized_log, PARSER_FAILURE_MARKERS):
        return CrawlResult(
            ResultKind.PARSER_FAILURE,
            detail="the crawl log contains a spider exception",
            failure_class=FailureClass.PARSER,
        )
    if _contains_any(normalized_log, TRANSPORT_FAILURE_MARKERS):
        return CrawlResult(
            ResultKind.TRANSPORT_FAILURE,
            detail="the crawl log contains a download or server failure",
            failure_class=FailureClass.TRANSPORT,
        )
    if crawl_exit_code != 0:
        return CrawlResult(
            ResultKind.TRANSPORT_FAILURE,
            detail=f"scrapy exited with code {crawl_exit_code}",
            failure_class=FailureClass.APPLICATION,
        )
    if not output_path.is_file():
        return CrawlResult(
            ResultKind.PARSER_FAILURE,
            detail="scrapy did not create the expected JSON feed",
            failure_class=FailureClass.PARSER,
        )

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return CrawlResult(
            ResultKind.PARSER_FAILURE,
            detail=f"the JSON feed could not be read: {error}",
            failure_class=FailureClass.PARSER,
        )

    if not isinstance(payload, list):
        return CrawlResult(
            ResultKind.PARSER_FAILURE,
            detail="the JSON feed root is not a list",
            failure_class=FailureClass.PARSER,
        )
    if any(not isinstance(item, dict) for item in payload):
        return CrawlResult(
            ResultKind.PARSER_FAILURE,
            detail="the JSON feed contains a non-object item",
            failure_class=FailureClass.PARSER,
        )
    if payload:
        return CrawlResult(ResultKind.SUCCESS_NON_EMPTY, item_count=len(payload))
    return CrawlResult(ResultKind.SUCCESS_EMPTY)


def _read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _print_result(result: CrawlResult, output_path: Path) -> None:
    messages = {
        ResultKind.SUCCESS_NON_EMPTY: "successful non-empty output",
        ResultKind.SUCCESS_EMPTY: "valid empty output",
        ResultKind.PARSER_FAILURE: "parser or feed failure",
        ResultKind.TRANSPORT_FAILURE: "transport failure",
        ResultKind.LIKELY_BLOCKING: "likely portal blocking",
    }
    print(f"RESULT: {result.kind.value} - {messages[result.kind]}")
    if result.detail:
        print(f"DETAIL: {result.detail}")
    if result.failure_class is not None:
        print(f"FAILURE_CLASS: {result.failure_class.value}")
    if result.kind is ResultKind.SUCCESS_NON_EMPTY:
        print(f"ITEMS: {result.item_count}")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        for listing in payload[:2]:
            print("-" * 50)
            for key in (
                "id",
                "price",
                "m2",
                "rooms",
                "town",
                "neighbour",
                "href",
                "site",
            ):
                if key in listing:
                    print(f"  {key:9}: {listing[key]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--crawl-exit-code", type=int, required=True)
    args = parser.parse_args()

    result = classify_crawl(
        output_path=args.output,
        log_text=_read_log(args.log),
        crawl_exit_code=args.crawl_exit_code,
    )
    _print_result(result, args.output)
    return 0 if result.kind in {
        ResultKind.SUCCESS_NON_EMPTY,
        ResultKind.SUCCESS_EMPTY,
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
