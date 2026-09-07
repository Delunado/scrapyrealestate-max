"""Run and record explicit, opt-in live probes for enabled portals."""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from scrapyrealestate.atomic_files import atomic_write_json, atomic_write_text
from scrapyrealestate.live_spider_result import CrawlResult, classify_crawl
from scrapyrealestate.runtime import DATA_DIR_ENV, RuntimePaths, get_runtime_paths


LIVE_SMOKE_ENV = "SCRAPYREALESTATE_RUN_LIVE_SMOKE"
DEFAULT_TIMEOUT_SECONDS = 180
MAX_RETAINED_LOG_CHARS = 500_000
DEFAULT_PROBES = {
    "idealista": (
        "idealista",
        "https://www.idealista.com/alquiler-viviendas/madrid-madrid/?ordenado-por=fecha-publicacion-desc",
    ),
    "idealista_proxy": (
        "idealista_proxy",
        "https://www.idealista.com/alquiler-viviendas/madrid-madrid/?ordenado-por=fecha-publicacion-desc",
    ),
    "pisoscom": (
        "pisoscom",
        "https://www.pisos.com/venta/pisos-madrid/fecharecientedesde-desc/",
    ),
    "habitaclia": (
        "habitaclia",
        "https://www.habitaclia.com/alquiler-madrid.htm?ordenar=mas_recientes",
    ),
    "fotocasa": (
        "fotocasa",
        "https://www.fotocasa.es/es/alquiler/viviendas/madrid-capital/todas-las-zonas/l",
    ),
    "yaencontre": (
        "yaencontre",
        "https://www.yaencontre.com/alquiler/pisos/madrid/o-recientes",
    ),
}


@dataclass(frozen=True, slots=True)
class LiveSmokeRecord:
    portal: str
    executed_at: str
    result: str
    failure_class: str | None
    item_count: int
    detail: str
    crawl_exit_code: int


def enabled_portals(database_path: Path) -> tuple[str, ...]:
    if not database_path.is_file():
        return ()
    with closing(
        sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    ) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT p.portal_key
            FROM search_portals AS p
            JOIN searches AS s ON s.id = p.search_id
            WHERE s.enabled = 1 AND p.enabled = 1
            ORDER BY p.portal_key
            """
        ).fetchall()
    return tuple(row[0] for row in rows)


def run_probe(
    portal: str,
    *,
    paths: RuntimePaths,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> LiveSmokeRecord:
    try:
        spider, url = DEFAULT_PROBES[portal]
    except KeyError as error:
        raise ValueError(f"no live probe is defined for portal {portal!r}") from error
    output = paths.live_test_output(spider)
    log = paths.live_test_log(spider)
    output.unlink(missing_ok=True)
    application_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment[DATA_DIR_ENV] = str(paths.data_dir)
    command = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        "-L",
        "INFO",
        spider,
        "-o",
        str(output),
        "-a",
        f"start_urls={url}",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=application_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        crawl_exit_code = completed.returncode
        log_text = f"{completed.stdout}\n{completed.stderr}"
    except subprocess.TimeoutExpired as error:
        crawl_exit_code = 124
        log_text = f"download timeout\n{error.stdout or ''}\n{error.stderr or ''}"
    result = classify_crawl(output, log_text, crawl_exit_code)
    atomic_write_text(log, _bounded_log(log_text))
    return _record(portal, result, crawl_exit_code)


def _record(portal: str, result: CrawlResult, exit_code: int) -> LiveSmokeRecord:
    return LiveSmokeRecord(
        portal=portal,
        executed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        result=result.kind.value,
        failure_class=result.failure_class.value if result.failure_class else None,
        item_count=result.item_count,
        detail=result.detail,
        crawl_exit_code=exit_code,
    )


def _bounded_log(value: str) -> str:
    if len(value) <= MAX_RETAINED_LOG_CHARS:
        return value
    return "[earlier live-smoke output truncated]\n" + value[-MAX_RETAINED_LOG_CHARS:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    arguments = parser.parse_args(argv)
    if os.environ.get(LIVE_SMOKE_ENV) != "1":
        parser.error(f"set {LIVE_SMOKE_ENV}=1 to authorize live portal requests")
    if arguments.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")

    paths = get_runtime_paths()
    portals = enabled_portals(paths.database_file)
    if not portals:
        parser.error("the configured database has no enabled portals")
    records = [
        run_probe(portal, paths=paths, timeout_seconds=arguments.timeout_seconds)
        for portal in portals
    ]
    report = arguments.report or paths.data_dir / "live-smoke-report.json"
    atomic_write_json(report, {"schema_version": "1.0", "probes": [asdict(item) for item in records]})
    for record in records:
        print(
            f"{record.portal}: {record.result}"
            + (f" ({record.failure_class})" if record.failure_class else "")
        )
    return 0 if all(record.failure_class is None for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
