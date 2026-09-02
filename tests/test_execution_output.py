from pathlib import Path

import pytest

from scrapyrealestate.execution.output import OutputDecodeError, read_jsonl_items


def test_missing_file_is_an_empty_result_not_an_error(tmp_path: Path):
    assert read_jsonl_items(tmp_path / "missing.jl") == ()


def test_reads_one_json_object_per_line(tmp_path: Path):
    path = tmp_path / "attempt.jl"
    path.write_text('{"id": "1", "price": "100000"}\n{"id": "2", "price": "200000"}\n', encoding="utf-8")

    items = read_jsonl_items(path)

    assert items == ({"id": "1", "price": "100000"}, {"id": "2", "price": "200000"})


def test_blank_lines_are_ignored(tmp_path: Path):
    path = tmp_path / "attempt.jl"
    path.write_text('{"id": "1"}\n\n   \n{"id": "2"}\n', encoding="utf-8")

    assert read_jsonl_items(path) == ({"id": "1"}, {"id": "2"})


def test_empty_file_is_an_empty_result(tmp_path: Path):
    path = tmp_path / "attempt.jl"
    path.write_text("", encoding="utf-8")

    assert read_jsonl_items(path) == ()


def test_preserves_non_ascii_content(tmp_path: Path):
    path = tmp_path / "attempt.jl"
    path.write_text('{"title": "Ático en Chamberí, 3 habitaciones"}\n', encoding="utf-8")

    items = read_jsonl_items(path)

    assert items == ({"title": "Ático en Chamberí, 3 habitaciones"},)


def test_malformed_json_line_raises_with_line_number(tmp_path: Path):
    path = tmp_path / "attempt.jl"
    path.write_text('{"id": "1"}\nnot json\n{"id": "3"}\n', encoding="utf-8")

    with pytest.raises(OutputDecodeError, match="line 2"):
        read_jsonl_items(path)


def test_a_json_value_that_is_not_an_object_raises(tmp_path: Path):
    path = tmp_path / "attempt.jl"
    path.write_text('["not", "an", "object"]\n', encoding="utf-8")

    with pytest.raises(OutputDecodeError, match="line 1"):
        read_jsonl_items(path)


def test_a_bare_json_scalar_line_raises(tmp_path: Path):
    path = tmp_path / "attempt.jl"
    path.write_text('"just a string"\n', encoding="utf-8")

    with pytest.raises(OutputDecodeError):
        read_jsonl_items(path)


def test_no_repair_of_concatenated_arrays_is_needed_or_attempted(tmp_path: Path):
    # The legacy shared-file pipeline concatenated crawls as "...][..." and
    # patched it back into one array; per-attempt JSON Lines output never
    # produces that shape, and this decoder must not try to guess at it.
    path = tmp_path / "attempt.jl"
    path.write_text("[]\n][\n[]\n", encoding="utf-8")

    with pytest.raises(OutputDecodeError):
        read_jsonl_items(path)
