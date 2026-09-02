import pytest

from scrapyrealestate.portals.location import slugify_location


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Madrid", "madrid"),
        ("  Madrid  ", "madrid"),
        ("Alcalá de Henares", "alcala-de-henares"),
        ("Sant Cugat del Vallès", "sant-cugat-del-valles"),
        ("A Coruña", "a-coruna"),
        ("Madrid, provincia", "madrid-provincia"),
        ("---Madrid---", "madrid"),
    ],
)
def test_slugify_location_lowercases_strips_accents_and_hyphenates(location, expected):
    assert slugify_location(location) == expected


@pytest.mark.parametrize("location", ["", "   ", "---", "¡¡¡"])
def test_slugify_location_rejects_text_with_no_usable_characters(location):
    with pytest.raises(ValueError, match="no usable text"):
        slugify_location(location)
