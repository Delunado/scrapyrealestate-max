from pathlib import Path

import pytest


MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "�")


@pytest.mark.parametrize(
    ("relative_path", "expected_text"),
    [
        ("README.md", "Configuración"),
        ("README.md", "deduplicación"),
        ("scrapyrealestate/scrapyrealestate/templates/info.html", "¡Configuración"),
    ],
)
def test_user_facing_files_are_valid_utf8_without_mojibake(
    relative_path: str, expected_text: str
):
    repository_root = Path(__file__).resolve().parents[1]
    contents = (repository_root / relative_path).read_text(encoding="utf-8")

    assert expected_text in contents
    assert not any(marker in contents for marker in MOJIBAKE_MARKERS)


@pytest.mark.parametrize(
    "relative_path",
    [
        "scrapyrealestate/scrapyrealestate/templates/index.html",
        "scrapyrealestate/scrapyrealestate/templates/info.html",
    ],
)
def test_templates_declare_utf8(relative_path: str):
    repository_root = Path(__file__).resolve().parents[1]
    contents = (repository_root / relative_path).read_text(encoding="utf-8")

    assert '<meta charset="utf-8">' in contents
