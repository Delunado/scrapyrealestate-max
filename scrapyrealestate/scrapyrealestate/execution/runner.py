"""Isolated ``scrapy crawl`` subprocess execution for one portal attempt.

Replaces the legacy ``main.run_spider``/``subprocess.run(..., check=False)``
call, which ignored the return code, had no timeout, and captured no
diagnostic output at all. ``SpiderRunner.run`` never raises: launching,
timing out, exiting non-zero, and producing unreadable output all become a
``PortalRunResult`` with the matching ``RunStatus`` instead, so one portal's
subprocess misbehaving can never take down the caller.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from scrapyrealestate.domain.values import RunStatus
from scrapyrealestate.execution.contract import PortalRunRequest, PortalRunResult, utc_now
from scrapyrealestate.execution.output import OutputDecodeError, read_jsonl_items

DEFAULT_MAX_DIAGNOSTIC_BYTES = 4_000
_KILL_GRACE_SECONDS = 5.0

BuildCommand = Callable[[PortalRunRequest], Sequence[str]]


def default_scrapy_command(
    request: PortalRunRequest, *, scrapy_executable: str = "scrapy"
) -> list[str]:
    """Build the ``scrapy crawl`` invocation for one isolated attempt.

    ``-o {path}:jsonlines`` writes strict JSON Lines to this attempt's own
    unique output file (see ``RuntimePaths.attempt_output``) rather than
    appending to a file shared across the whole search run.
    """
    return [
        scrapy_executable,
        "crawl",
        "-L",
        request.log_level,
        request.spider_name,
        "-o",
        f"{request.output_path}:jsonlines",
        "-a",
        f"start_urls={request.start_url}",
    ]


class SpiderRunner:
    """Runs one portal attempt as an isolated, bounded subprocess.

    Every attempt gets its own timeout, its own truncated stderr capture,
    and best-effort cleanup of the child (and, on POSIX, its whole process
    group) if it must be killed.
    """

    def __init__(
        self,
        *,
        working_directory: Path,
        build_command: BuildCommand | None = None,
        max_diagnostic_bytes: int = DEFAULT_MAX_DIAGNOSTIC_BYTES,
    ) -> None:
        self._working_directory = working_directory
        self._build_command = build_command or default_scrapy_command
        self._max_diagnostic_bytes = max_diagnostic_bytes

    def run(self, request: PortalRunRequest) -> PortalRunResult:
        started_at = utc_now()
        command = list(self._build_command(request))

        popen_kwargs: dict[str, object] = dict(
            cwd=str(self._working_directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )

        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except OSError as error:
            return self._result(
                request,
                started_at,
                utc_now(),
                RunStatus.TRANSPORT_ERROR,
                diagnostic=f"could not launch spider process: {error}",
            )

        try:
            _stdout, stderr = process.communicate(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._kill(process)
            return self._result(
                request,
                started_at,
                utc_now(),
                RunStatus.TIMEOUT,
                diagnostic=f"spider timed out after {request.timeout_seconds:.0f}s",
            )

        finished_at = utc_now()
        diagnostic = self._bounded(stderr) or None

        if process.returncode != 0:
            return self._result(
                request,
                started_at,
                finished_at,
                RunStatus.TRANSPORT_ERROR,
                return_code=process.returncode,
                diagnostic=diagnostic,
            )

        try:
            items = read_jsonl_items(request.output_path)
        except OutputDecodeError as error:
            return self._result(
                request,
                started_at,
                finished_at,
                RunStatus.PARSER_ERROR,
                return_code=process.returncode,
                diagnostic=str(error),
            )

        status = RunStatus.SUCCESS if items else RunStatus.EMPTY
        return PortalRunResult(
            portal=request.portal,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            items=items,
            return_code=process.returncode,
            diagnostic=diagnostic,
        )

    def _kill(self, process: subprocess.Popen) -> None:
        """Best-effort termination of the child and, on POSIX, its group."""
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        else:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.communicate(timeout=_KILL_GRACE_SECONDS)
        except (subprocess.TimeoutExpired, ValueError):
            pass

    def _bounded(self, text: str | None) -> str:
        if not text:
            return ""
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= self._max_diagnostic_bytes:
            return text
        truncated = encoded[: self._max_diagnostic_bytes].decode("utf-8", errors="ignore")
        return f"{truncated}\n... [truncated]"

    def _result(
        self,
        request: PortalRunRequest,
        started_at,
        finished_at,
        status: RunStatus,
        *,
        return_code: int | None = None,
        diagnostic: str | None = None,
    ) -> PortalRunResult:
        return PortalRunResult(
            portal=request.portal,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            return_code=return_code,
            diagnostic=diagnostic,
        )
