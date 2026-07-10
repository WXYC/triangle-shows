"""Per-scraper identity-field behavior (issue #8).

Verifies that the fields reconciliation keys on — external_id and source_url —
are identity-safe as they leave each scraper's parse layer: no blank ids, no
shared listing-page URLs masquerading as per-event identity. Pure unit tests:
parse methods are fed constructed dicts/HTML, no database, no HTTP.
"""

from datetime import date

import pytest

from app.scrapers.base import ScrapedEvent


def _event(**overrides) -> ScrapedEvent:
    fields = dict(
        name="Juana Molina",
        date=date(2026, 9, 1),
        venue_slug="cats-cradle",
        source="manual",
    )
    fields.update(overrides)
    return ScrapedEvent(**fields)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Blank ids must never become identity keys — they'd reconcile every
        # event at the venue onto one row.
        ("", None),
        ("   ", None),
        (None, None),
        # Real ids pass through untouched.
        ("39482", "39482"),
    ],
)
def test_scraped_event_coerces_blank_external_id(raw, expected):
    assert _event(external_id=raw).external_id == expected


# --- VenuePilot: raw GraphQL id → external_id ---


@pytest.mark.parametrize(
    ("raw_id", "expected"),
    [
        # JSON null must not become the string "None" (str(None) footgun).
        (None, None),
        # Absent key → no identity.
        ("__absent__", None),
        # Numeric ids arrive as ints from GraphQL; stored as their string form.
        (39482, "39482"),
        ("39482", "39482"),
    ],
)
def test_venuepilot_external_id_coercion(raw_id, expected):
    from app.scrapers.venuepilot import VenuePilotScraper

    item = {"name": "Chuquimamani-Condori", "date": "2026-09-01"}
    if raw_id != "__absent__":
        item["id"] = raw_id

    scraper = VenuePilotScraper("test-venue", {"account_id": "1"})
    parsed = scraper._parse_event(item)
    assert parsed is not None
    assert parsed.external_id == expected


# --- MEC: source_url must be per-event, never the shared listing page ---


def _mec_scraper():
    from app.scrapers.mec import MECScraper

    return MECScraper("test-venue", {"url": "https://venue.com/events/"})


def test_mec_event_own_jsonld_url_wins_over_page_url():
    data = {
        "@type": "Event",
        "name": "Jessica Pratt",
        "startDate": "2026-09-01",
        "url": "https://venue.com/event/jessica-pratt/",
    }
    parsed = _mec_scraper()._parse_jsonld_event(
        data, page_url="https://venue.com/event/other-page/", page_url_is_event=True
    )
    assert parsed.source_url == "https://venue.com/event/jessica-pratt/"


def test_mec_listing_page_url_never_becomes_identity():
    # Listing-parsed event with no JSON-LD url: the shared listing URL must not
    # become source_url (it would alias every event on the page), but it may
    # still serve as the ticket-link fallback.
    data = {"@type": "Event", "name": "Jessica Pratt", "startDate": "2026-09-01"}
    parsed = _mec_scraper()._parse_jsonld_event(
        data, page_url="https://venue.com/events/", page_url_is_event=False
    )
    assert parsed.source_url is None
    assert parsed.ticket_url == "https://venue.com/events/"


def test_mec_detail_page_url_is_identity_fallback():
    data = {"@type": "Event", "name": "Jessica Pratt", "startDate": "2026-09-01"}
    parsed = _mec_scraper()._parse_jsonld_event(
        data, page_url="https://venue.com/event/jessica-pratt/", page_url_is_event=True
    )
    assert parsed.source_url == "https://venue.com/event/jessica-pratt/"


# --- Koka Booth: shared page_url must not become identity ---


def _koka_scraper():
    from app.scrapers.koka_booth import KokaBoothScraper

    return KokaBoothScraper("koka-booth", {"url": "https://boothamphitheatre.com/events/"})


def test_koka_booth_own_jsonld_url_is_identity():
    item = {
        "name": "Duke Ellington Orchestra",
        "startDate": "2026-09-01",
        "url": "https://boothamphitheatre.com/event/duke-ellington/",
    }
    parsed = _koka_scraper()._parse_jsonld(item, page_url="https://boothamphitheatre.com/events/")
    assert parsed.source_url == "https://boothamphitheatre.com/event/duke-ellington/"


def test_koka_booth_shared_page_url_never_becomes_identity():
    item = {"name": "Duke Ellington Orchestra", "startDate": "2026-09-01"}
    parsed = _koka_scraper()._parse_jsonld(item, page_url="https://boothamphitheatre.com/events/")
    assert parsed.source_url is None
    # The listing URL remains a legitimate ticket-link fallback.
    assert parsed.ticket_url == "https://boothamphitheatre.com/events/"


# --- Manager dispatch stays keyed on Venue.scraper_type ---


def test_manager_dispatch_resolves_registry_types():
    from types import SimpleNamespace

    from app.scrapers.manager import ScrapeManager
    from app.scrapers.mec import MECScraper
    from app.scrapers.venuepilot import VenuePilotScraper

    manager = ScrapeManager(session=None)

    def venue(scraper_type):
        return SimpleNamespace(
            slug="test-venue", scraper_type=scraper_type,
            scraper_config={"url": "https://x/", "account_id": "1"},
            ticketmaster_venue_id=None,
        )

    assert isinstance(manager._get_scraper(venue("venuepilot")), VenuePilotScraper)
    assert isinstance(manager._get_scraper(venue("mec")), MECScraper)
    assert manager._get_scraper(venue("no-such-type")) is None


def test_scraped_event_stringifies_numeric_external_id():
    # Numeric ids from JSON APIs must not crash the per-event parse (a silent
    # per-event drop) — they coerce to their string form.
    assert _event(external_id=39482).external_id == "39482"


# --- Tribe Events: per-event JSON-LD url must win over the detail-page href ---


def _tribe_scraper():
    from app.scrapers.tribe_events import TribeEventsScraper

    return TribeEventsScraper("test-venue", {"url": "https://venue.com/events/"})


def test_tribe_event_own_jsonld_url_wins_over_detail_href():
    # The Events Calendar emits occurrence-specific URLs; a detail page can embed
    # several Event items, so the passed href must not override data["url"].
    data = {
        "@type": "Event",
        "name": "Jessica Pratt",
        "startDate": "2026-09-01",
        "url": "https://venue.com/event/jessica-pratt/2026-09-01/",
    }
    parsed = _tribe_scraper()._parse_jsonld_event(data, source_url="https://venue.com/event/jessica-pratt/")
    assert parsed.source_url == "https://venue.com/event/jessica-pratt/2026-09-01/"


def test_tribe_detail_href_is_fallback_when_jsonld_lacks_url():
    data = {"@type": "Event", "name": "Jessica Pratt", "startDate": "2026-09-01"}
    parsed = _tribe_scraper()._parse_jsonld_event(data, source_url="https://venue.com/event/jessica-pratt/")
    assert parsed.source_url == "https://venue.com/event/jessica-pratt/"


def test_scraped_event_coerces_non_string_source_url_to_none():
    # Malformed JSON-LD can put a dict/list where a URL string belongs (e.g.
    # "url": {"@id": ...}); it must not survive into identity derivation or a
    # varchar column bind.
    assert _event(source_url={"@id": "https://x"}).source_url is None
    assert _event(source_url=["https://x"]).source_url is None
    assert _event(source_url="https://x").source_url == "https://x"
