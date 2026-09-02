from pathlib import Path

import pytest

from scrapyrealestate.runtime import DATA_DIR_ENV, RuntimePaths


def test_runtime_paths_keep_absolute_legacy_default(tmp_path: Path):
    paths = RuntimePaths.from_environment({}, cwd=tmp_path)

    assert paths.data_dir == (tmp_path / "data").resolve()
    assert paths.config_file == paths.data_dir / "config.json"
    assert paths.ids_file == paths.data_dir / "ids.json"
    assert paths.user_agent_file == paths.data_dir / "useragent.txt"
    assert paths.crawl_output("my_search") == paths.data_dir / "my_search.json"
    assert paths.live_test_output("pisoscom") == paths.data_dir / "test_pisoscom.json"
    assert paths.live_test_log("pisoscom") == paths.data_dir / "test_pisoscom.log"


def test_runtime_paths_accept_configured_absolute_data_directory(tmp_path: Path):
    configured = (tmp_path / "persistent-data").resolve()

    paths = RuntimePaths.from_environment({DATA_DIR_ENV: str(configured)})

    assert paths.data_dir == configured


def test_runtime_paths_reject_relative_configured_directory():
    with pytest.raises(ValueError, match=f"{DATA_DIR_ENV} must be an absolute path"):
        RuntimePaths.from_environment({DATA_DIR_ENV: "relative/data"})


def test_runtime_paths_create_configured_directory(tmp_path: Path):
    data_dir = (tmp_path / "nested" / "data").resolve()
    paths = RuntimePaths(data_dir)

    assert paths.ensure_data_dir() == data_dir
    assert data_dir.is_dir()
