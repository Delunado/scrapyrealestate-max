"""Opt-in execution of the deterministic fixture soak inside the built image."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUN_DOCKER_SOAK_ENV = "SCRAPYREALESTATE_RUN_DOCKER_SOAK"


@pytest.mark.soak
def test_container_fixture_soak():
    if os.environ.get(RUN_DOCKER_SOAK_ENV) != "1":
        pytest.skip(f"set {RUN_DOCKER_SOAK_ENV}=1 to run the container soak")
    _require_docker_daemon()

    suffix = uuid4().hex[:10]
    volume = f"scrapyrealestate-soak-{suffix}"
    container = f"scrapyrealestate-soak-{suffix}"
    try:
        _run(["docker", "compose", "build", "scrapyrealestate"], timeout=600)
        _run(["docker", "volume", "create", volume])
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{volume}:/data",
                "alpine",
                "chown",
                "10001:10001",
                "/data",
            ]
        )
        completed = _run(
            [
                "docker",
                "run",
                "--name",
                container,
                "--volume",
                f"{volume}:/var/lib/scrapyrealestate",
                "--entrypoint",
                "python",
                "scrapyrealestate:local",
                "-m",
                "scrapyrealestate.soak",
                "--cycles",
                "20",
            ],
            timeout=180,
        )
        assert "fixture soak passed" in completed.stdout
        state = _run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container]
        )
        assert state.stdout.strip() == "false"
        report_result = _run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{volume}:/data:ro",
                "alpine",
                "cat",
                "/data/soak-report.json",
            ]
        )
        report = json.loads(report_result.stdout)
        assert tuple(report["scheduled_runs"].values()) == (20, 10, 6)
        assert report["same_search_conflicts"] == 1
        assert set(report["max_overlap_per_search"].values()) == {1}
        assert report["database_integrity"] == "ok"
        assert report["notification_event_count"] == 3
        assert report["succeeded_delivery_count"] == 3
        assert report["duplicate_delivery_pairs"] == 0
        assert report["active_fixture_attempts"] == 0
    finally:
        _run(["docker", "rm", "--force", container], check=False)
        _run(["docker", "volume", "rm", "--force", volume], check=False)


def _require_docker_daemon() -> None:
    result = _run(["docker", "info"], check=False, timeout=30)
    if result.returncode != 0:
        pytest.skip("Docker daemon is not available")


def _run(
    command: list[str], *, timeout: float = 60, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
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
