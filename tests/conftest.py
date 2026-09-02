from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ROOT = REPOSITORY_ROOT / "scrapyrealestate"
FIXTURES_ROOT = Path(__file__).parent / "fixtures"


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


@pytest.fixture
def load_fixture():
    """Return a loader for UTF-8 fixture files stored below ``tests/fixtures``."""

    def load(relative_path: str) -> str:
        return (FIXTURES_ROOT / relative_path).read_text(encoding="utf-8")

    return load

