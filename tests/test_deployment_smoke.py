"""Opt-in verification of the persistent Docker Compose deployment.

Run with ``SCRAPYREALESTATE_RUN_DOCKER_SMOKE=1 python -m pytest -m deployment``
on a host with a running Docker daemon. The default offline suite intentionally
does not build images or contact a Docker daemon.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUN_DOCKER_SMOKE_ENV = "SCRAPYREALESTATE_RUN_DOCKER_SMOKE"


@pytest.mark.deployment
def test_compose_recreate_preserves_imported_configuration_and_history():
    """A fresh container sees the same volume data after recreation.

    Legacy files are deliberately used only to seed a configuration and history;
    startup imports them into SQLite, which is what the recreated container proves
    persists. The 10-minute interval ensures this test never launches a live crawl.
    """
    if os.environ.get(RUN_DOCKER_SMOKE_ENV) != "1":
        pytest.skip(f"set {RUN_DOCKER_SMOKE_ENV}=1 to run Docker deployment smoke")
    _require_docker_daemon()

    project = f"scrapyrealestate-smoke-{uuid4().hex[:10]}"
    volume = f"{project}-data"
    host_port = _free_local_port()
    environment = os.environ | {
        "SCRAPYREALESTATE_DATA_VOLUME": volume,
        "SCRAPYREALESTATE_WEB_PORT": str(host_port),
    }
    compose = ["docker", "compose", "--project-name", project]
    try:
        _run(["docker", "volume", "create", volume])
        _seed_legacy_sources(volume)
        _run([*compose, "up", "--build", "--detach"], env=environment, timeout=600)
        _wait_for_ready(host_port)
        _assert_persisted_import(compose, environment)

        # This exercises the Compose stop grace period while the scheduler is idle.
        _run([*compose, "stop"], env=environment, timeout=45)
        status = _run([*compose, "ps", "--status", "running", "--quiet"], env=environment)
        assert status.stdout.strip() == ""
        _run([*compose, "start"], env=environment, timeout=45)
        _wait_for_ready(host_port)

        _run(
            [*compose, "up", "--detach", "--force-recreate"],
            env=environment,
            timeout=120,
        )
        _wait_for_ready(host_port)
        _assert_persisted_import(compose, environment)
    finally:
        _run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            env=environment,
            check=False,
        )
        _run(["docker", "volume", "rm", "--force", volume], check=False)


def _require_docker_daemon() -> None:
    if not _command_exists("docker"):
        pytest.skip("Docker CLI is not installed")
    result = _run(["docker", "info"], check=False, timeout=30)
    if result.returncode != 0:
        pytest.skip("Docker daemon is not available")


def _seed_legacy_sources(volume: str) -> None:
    config = {
        "scrapy_rs_name": "Docker smoke search",
        "time_update": "600",
        "url_pisoscom": "https://www.pisos.com/alquiler/pisos-madrid/",
    }
    payloads = {
        "config.json": json.dumps(config, separators=(",", ":")),
        "ids.json": "[123456]",
    }
    for filename, contents in payloads.items():
        encoded = base64.b64encode(contents.encode("utf-8")).decode("ascii")
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{volume}:/data",
                "alpine",
                "sh",
                "-c",
                f"echo {encoded} | base64 -d > /data/{filename}",
            ]
        )
    # The application image intentionally runs as this unprivileged identity.
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{volume}:/data",
            "alpine",
            "chown",
            "-R",
            "10001:10001",
            "/data",
        ]
    )


def _assert_persisted_import(compose: list[str], environment: dict[str, str]) -> None:
    result = _run(
        [
            *compose,
            "exec",
            "--no-TTY",
            "scrapyrealestate",
            "python",
            "-c",
            (
                "import sqlite3; "
                "db = sqlite3.connect('/var/lib/scrapyrealestate/scrapyrealestate.sqlite3'); "
                "print(db.execute('SELECT count(*) FROM searches').fetchone()[0]); "
                "print(db.execute('SELECT count(*) FROM legacy_seen_ids').fetchone()[0])"
            ),
        ],
        env=environment,
    )
    assert result.stdout.splitlines() == ["1", "1"]


def _wait_for_ready(port: int, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/readyz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    pytest.fail(f"deployment did not become ready within {timeout:.0f} seconds")


def _free_local_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _command_exists(command: str) -> bool:
    return any(
        (directory / f"{command}{suffix}").is_file()
        for directory in map(Path, os.environ.get("PATH", "").split(os.pathsep))
        for suffix in ("", ".exe")
    )


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        pytest.fail(f"Docker command failed: {' '.join(command)}\n{result.stdout}")
    return result
