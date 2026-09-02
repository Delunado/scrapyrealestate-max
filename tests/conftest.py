from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ROOT = REPOSITORY_ROOT / "scrapyrealestate"


@pytest.fixture
def application_root() -> Path:
    """Return the directory from which the legacy application is run."""
    return APPLICATION_ROOT


@pytest.fixture
def temporary_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run a test with an isolated legacy-compatible ``./data`` directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    return data_dir

