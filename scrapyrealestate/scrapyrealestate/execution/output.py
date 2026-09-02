"""Strict JSON Lines decoding for per-attempt spider output.

The legacy pipeline (see ``main.scrap_realestate``) appends every crawl's
``-o`` output to one shared file and then repairs the concatenated JSON
arrays that produces (``\\n][`` -> ``,``) before parsing it. Isolated,
per-attempt output (``RuntimePaths.attempt_output``) removes the need for
that repair step entirely: each attempt gets its own JSON Lines file
(``scrapy crawl ... -o path.jl``, one JSON object per line), so decoding it
can be strict instead of tolerant of a hand-patched array.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class OutputDecodeError(ValueError):
    """A per-attempt output file is not valid JSON Lines."""


def read_jsonl_items(path: Path) -> tuple[dict[str, Any], ...]:
    """Strictly decode one JSON object per non-blank line of ``path``.

    A missing file is a legitimate empty result (no attempt output was ever
    written, e.g. the spider crashed before writing anything) and returns an
    empty tuple rather than raising. Any line that is present but is not
    valid JSON, or that decodes to something other than a JSON object,
    raises ``OutputDecodeError`` so the caller can classify the attempt as a
    parser failure instead of silently losing or misinterpreting data.
    """
    if not path.exists():
        return ()
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise OutputDecodeError(
                    f"{path.name}: line {line_number} is not valid JSON: {error}"
                ) from error
            if not isinstance(value, dict):
                raise OutputDecodeError(
                    f"{path.name}: line {line_number} is not a JSON object"
                )
            items.append(value)
    return tuple(items)
