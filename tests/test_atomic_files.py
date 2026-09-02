import json
from pathlib import Path

import pytest

from scrapyrealestate import atomic_files
from scrapyrealestate.atomic_files import atomic_write_json, atomic_write_text


def test_atomic_write_text_creates_parent_and_replaces_existing_file(tmp_path: Path):
    destination = tmp_path / "nested" / "config.json"
    destination.parent.mkdir()
    destination.write_text("old", encoding="utf-8")

    atomic_write_text(destination, "configuración nueva")

    assert destination.read_text(encoding="utf-8") == "configuración nueva"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_write_json_retains_legacy_json_shape(tmp_path: Path):
    destination = tmp_path / "data" / "ids.json"

    atomic_write_json(destination, [10, 20, 30])

    assert json.loads(destination.read_text(encoding="utf-8")) == [10, 20, 30]


def test_failed_atomic_replace_preserves_previous_file_and_cleans_temp(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "ids.json"
    destination.write_text("[1]", encoding="utf-8")

    def fail_replace(unused_source, unused_destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_files.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_json(destination, [1, 2])

    assert destination.read_text(encoding="utf-8") == "[1]"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []
