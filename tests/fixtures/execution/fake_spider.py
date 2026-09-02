"""Test double for a ``scrapy crawl`` subprocess, used by SpiderRunner tests.

Invoked as ``python fake_spider.py <mode> [output_path]`` so tests can drive
every outcome SpiderRunner must classify without needing Scrapy installed or
a real portal to reach.
"""

import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    mode = argv[0]

    if mode == "success":
        Path(argv[1]).write_text('{"id": "1"}\n{"id": "2"}\n', encoding="utf-8")
        return 0

    if mode == "empty":
        Path(argv[1]).write_text("", encoding="utf-8")
        return 0

    if mode == "fail":
        sys.stderr.write("crawl failed\n" * 1000)
        return 1

    if mode == "malformed":
        Path(argv[1]).write_text("not json\n", encoding="utf-8")
        return 0

    if mode == "hang":
        # Sleeps well past any test timeout, then leaves evidence (a marker
        # file) only if it was allowed to run to completion - proving
        # whether the runner actually killed it on timeout.
        time.sleep(5)
        Path(argv[1]).write_text("hang completed\n", encoding="utf-8")
        return 0

    raise SystemExit(f"unknown fake_spider mode: {mode!r}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
