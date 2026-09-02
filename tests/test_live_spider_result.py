import json

import pytest

from scrapyrealestate.live_spider_result import ResultKind, classify_crawl


@pytest.mark.parametrize(
    ("payload", "expected_kind", "expected_count"),
    [
        ([{"id": "123"}], ResultKind.SUCCESS_NON_EMPTY, 1),
        ([], ResultKind.SUCCESS_EMPTY, 0),
    ],
)
def test_classify_successful_json_feed(
    tmp_path, payload, expected_kind, expected_count
):
    output = tmp_path / "result.json"
    output.write_text(json.dumps(payload), encoding="utf-8")

    result = classify_crawl(output, "Spider closed (finished)", 0)

    assert result.kind is expected_kind
    assert result.item_count == expected_count


@pytest.mark.parametrize(
    ("log_text", "crawl_exit_code", "expected_kind"),
    [
        (
            "IDEALISTA: respuesta de desafío detectada (posible bloqueo anti-bot)",
            0,
            ResultKind.LIKELY_BLOCKING,
        ),
        (
            "ERROR Spider error processing request\nspider_exceptions/KeyError: 1",
            0,
            ResultKind.PARSER_FAILURE,
        ),
        (
            "downloader/exception_type_count/twisted.internet.error.DNSLookupError",
            0,
            ResultKind.TRANSPORT_FAILURE,
        ),
        (
            "Error al obtener datos de fotocasa.es: selector wait timed out",
            0,
            ResultKind.TRANSPORT_FAILURE,
        ),
        ("Spider closed unexpectedly", 2, ResultKind.TRANSPORT_FAILURE),
    ],
)
def test_classify_log_and_exit_failures(
    tmp_path, log_text, crawl_exit_code, expected_kind
):
    output = tmp_path / "result.json"
    output.write_text("[]", encoding="utf-8")

    result = classify_crawl(output, log_text, crawl_exit_code)

    assert result.kind is expected_kind


@pytest.mark.parametrize(
    ("contents", "create_output"),
    [
        ("{not valid JSON", True),
        ('{"id": "not-a-list"}', True),
        ('["not-an-object"]', True),
        ("", False),
    ],
)
def test_classify_missing_or_invalid_feed_as_parser_failure(
    tmp_path, contents, create_output
):
    output = tmp_path / "result.json"
    if create_output:
        output.write_text(contents, encoding="utf-8")

    result = classify_crawl(output, "Spider closed (finished)", 0)

    assert result.kind is ResultKind.PARSER_FAILURE
