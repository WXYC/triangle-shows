"""Unit tests for the event-identity helpers (app/scrapers/identity.py).

These functions define the stable-identity contract from issue #8: how a scraped
source_url is normalized into a host-independent identity string, and how the
tier-prefixed source_key (ext: / url: / hash:) is derived. Pure functions — no
database, no HTTP.
"""

import pytest

from app.scrapers.identity import UrlIdentityVerdict, derive_source_key, normalize_source_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # No URL → no identity; empty/whitespace are treated the same as missing.
        (None, None),
        ("", None),
        ("   ", None),
        # Scheme and host are stripped: CDN or domain migrations must not change identity.
        ("https://catscradle.com/event/juana-molina/", "/event/juana-molina"),
        ("http://www.catscradle.com/event/juana-molina", "/event/juana-molina"),
        ("https://cdn2.catscradle.com/event/juana-molina", "/event/juana-molina"),
        # Fragments are presentation-only.
        ("https://host/path#tickets", "/path"),
        # Query strings carry identity on ticketing pages — kept.
        ("https://host/path?eid=7", "/path?eid=7"),
        # ...but known tracking params are stripped, preserving the rest.
        (
            "https://host/path?utm_source=x&eid=7&fbclid=y&utm_medium=z&gclid=q",
            "/path?eid=7",
        ),
        # A URL that is ONLY tracking params normalizes to the bare path.
        ("https://host/path?utm_source=x", "/path"),
        # Query-parameter order must not change identity — the same page can
        # emit its params in either order across scrapes.
        ("https://host/path?venue=k&eid=7", "/path?eid=7&venue=k"),
        ("https://host/path?eid=7&venue=k", "/path?eid=7&venue=k"),
        # Trailing slash is insignificant.
        ("https://host/path/", "/path"),
        # Bare host → root path, still a usable identity.
        ("https://host", "/"),
        ("https://host/", "/"),
    ],
)
def test_normalize_source_url(raw, expected):
    assert normalize_source_url(raw) == expected


HASH = "a" * 64


@pytest.mark.parametrize(
    ("external_id", "normalized_url", "verdict", "expected"),
    [
        # Tier 1: an external id always wins, regardless of URL or verdict.
        ("39482", "/event/foo", UrlIdentityVerdict.TRUSTED, "ext:39482"),
        ("39482", None, UrlIdentityVerdict.HASH_FALLBACK, "ext:39482"),
        # Tier 2: normalized URL, only when the scraper's verdict trusts URLs.
        (None, "/event/foo?eid=7", UrlIdentityVerdict.TRUSTED, "url:/event/foo?eid=7"),
        # HASH_FALLBACK verdict skips the URL tier even when a URL is present.
        (None, "/event/foo", UrlIdentityVerdict.HASH_FALLBACK, f"hash:{HASH}"),
        # Tier 3: no ext, no usable URL → content hash.
        (None, None, UrlIdentityVerdict.TRUSTED, f"hash:{HASH}"),
    ],
)
def test_derive_source_key_precedence(external_id, normalized_url, verdict, expected):
    assert derive_source_key(external_id, normalized_url, HASH, verdict) == expected


# --- Audit registry: every scraper carries an explicit identity verdict ---


def _discover_scraper_classes():
    """Find every BaseScraper subclass defined in app/scrapers/*.py."""
    import importlib
    import inspect
    import pkgutil

    import app.scrapers as pkg
    from app.scrapers.base import BaseScraper

    classes = set()
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        module = importlib.import_module(f"app.scrapers.{mod_info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseScraper) and obj is not BaseScraper and obj.__module__ == module.__name__:
                classes.add(obj)
    return classes


def test_every_scraper_is_registered_with_a_verdict():
    """The registry is what gates reconciliation, the migration, and the backfill —
    a scraper missing from it (or without an explicit verdict) silently gets
    hash-only identity, so drift is a test failure, not a runtime surprise."""
    from app.scrapers.identity import SCRAPER_REGISTRY, UrlIdentityVerdict, scraper_class

    discovered = _discover_scraper_classes()
    registered = {scraper_class(t) for t in SCRAPER_REGISTRY}
    assert registered == discovered

    for cls in discovered:
        # The verdict must be declared on the class itself, not inherited — an
        # audit is an explicit judgment, never a default.
        assert isinstance(cls.__dict__.get("URL_IDENTITY"), UrlIdentityVerdict), (
            f"{cls.__name__} must declare URL_IDENTITY with a one-line justification"
        )


def test_unknown_scraper_type_gets_safe_verdict():
    from app.scrapers.identity import UrlIdentityVerdict, url_identity_verdict

    assert url_identity_verdict("some-future-scraper") is UrlIdentityVerdict.HASH_FALLBACK


def test_normalize_source_url_tolerates_non_string_input():
    """Defense in depth: a non-str reaching the normalizer means a scraper let
    malformed JSON-LD through — that must cost URL-tier identity, not the whole
    venue's scrape cycle."""
    assert normalize_source_url({"@id": "https://x"}) is None
    assert normalize_source_url(123) is None
