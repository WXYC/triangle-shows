"""Shared schema.org image normalization: ``BaseScraper.extract_schema_image``.

Pure unit tests for the one helper the JSON-LD scrapers (tickpick_organizer,
mec, koka_booth) share for turning schema.org's polymorphic ``image`` property
into a single URL string or ``None``. schema.org lets ``image`` be a bare URL
string, a list of those, an ImageObject dict (``{"url": ...}``), or a list of
ImageObject dicts; anything else — or an empty/whitespace value — degrades to
``None`` rather than raising, so a malformed feed can't crash a per-event parse.
"""

from app.scrapers.base import BaseScraper

_URL = "https://static-o.tickpick.com/poster.jpg"


def test_bare_string():
    assert BaseScraper.extract_schema_image(_URL) == _URL


def test_list_of_strings_takes_first():
    assert BaseScraper.extract_schema_image([_URL, "https://x/2.jpg"]) == _URL


def test_image_object_dict():
    assert (
        BaseScraper.extract_schema_image({"@type": "ImageObject", "url": _URL}) == _URL
    )


def test_list_of_image_objects_takes_first_url():
    data = [
        {"@type": "ImageObject", "url": _URL},
        {"@type": "ImageObject", "url": "https://x/2.jpg"},
    ]
    assert BaseScraper.extract_schema_image(data) == _URL


def test_none_is_none():
    assert BaseScraper.extract_schema_image(None) is None


def test_missing_key_pattern_is_none():
    # Callers pass data.get("image"); an absent key yields None.
    assert BaseScraper.extract_schema_image({}.get("image")) is None


def test_empty_list_is_none():
    assert BaseScraper.extract_schema_image([]) is None


def test_empty_string_is_none():
    assert BaseScraper.extract_schema_image("") is None


def test_whitespace_string_is_none():
    assert BaseScraper.extract_schema_image("   ") is None
    assert BaseScraper.extract_schema_image("\n\t") is None


def test_surrounding_whitespace_is_trimmed():
    assert BaseScraper.extract_schema_image(f"  {_URL}  ") == _URL


def test_image_object_without_url_is_none():
    assert BaseScraper.extract_schema_image({"@type": "ImageObject"}) is None


def test_unrecognized_shape_is_none():
    assert BaseScraper.extract_schema_image(123) is None
    assert BaseScraper.extract_schema_image({"url": {"@id": "x"}}) is None
    assert BaseScraper.extract_schema_image([[_URL]]) is None
