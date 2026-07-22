"""TickPick organizer scraper (Chapel of Bones): parse-layer behavior.

Pure unit tests — ``_parse_event`` is fed a JSON-LD dict shaped like the live
https://www.tickpick.com/organizer/o/chapel-of-bones response, so there is no
database and no HTTP. The scraper fetches via the shared BaseScraper.fetch_soup
path (current-Chrome browser headers); see tests/test_fetch_soup.py for that
shared behavior.

schema.org's ``image`` property isn't a single shape: it may be a bare URL
string, a list of URL strings, or an ImageObject dict (``{"url": ...}``). A
live fetch of the Chapel of Bones organizer page (2026-07-21) confirmed the
JSON-LD is reachable (no WAF block), but none of that page's 36 current events
populate ``image`` at all — only the top-level Organization.logo does, as a
bare string. So the "image absent" fixture below is confirmed against live
data; the string/list/ImageObject "image present" fixtures are constructed
from the schema.org spec and should be reconfirmed against production once
TickPick populates a per-event image.
"""

from datetime import date, timedelta

from app.scrapers.tickpick_organizer import TickPickOrganizerScraper

_FUTURE = (date.today() + timedelta(days=30)).isoformat()

# Modeled on the live JSON-LD nested under Organization.event at
# https://www.tickpick.com/organizer/o/chapel-of-bones (fetched 2026-07-21):
# real keys (@type, location, name, startDate, url) for a single Event, with a
# WXYC-representative act (Cat Power) swapped in for the name and an `image`
# key layered on per the shape under test.
_BASE_EVENT = {
    "@type": "Event",
    "location": {
        "@type": "Place",
        "name": "Chapel of Bones",
        "address": "600 Glenwood Ave, Raleigh, NC 27603",
    },
    "name": "Cat Power",
    "startDate": f"{_FUTURE}T20:00:00Z",
    "url": "https://www.tickpick.com/organizer/event/cat-power-12345678",
}


def _scraper() -> TickPickOrganizerScraper:
    return TickPickOrganizerScraper(
        "chapel-of-bones", {"organizer_id": "chapel-of-bones"}
    )


def test_parse_event_extracts_string_image():
    data = {**_BASE_EVENT, "image": "https://static-o.tickpick.com/poster.jpg"}
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.image_url == "https://static-o.tickpick.com/poster.jpg"


def test_parse_event_extracts_first_image_from_list():
    data = {
        **_BASE_EVENT,
        "image": [
            "https://static-o.tickpick.com/poster-1.jpg",
            "https://static-o.tickpick.com/poster-2.jpg",
        ],
    }
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.image_url == "https://static-o.tickpick.com/poster-1.jpg"


def test_parse_event_extracts_url_from_image_object():
    data = {
        **_BASE_EVENT,
        "image": {
            "@type": "ImageObject",
            "url": "https://static-o.tickpick.com/poster.jpg",
        },
    }
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.image_url == "https://static-o.tickpick.com/poster.jpg"


def test_parse_event_extracts_url_from_list_of_image_objects():
    data = {
        **_BASE_EVENT,
        "image": [
            {"@type": "ImageObject", "url": "https://static-o.tickpick.com/poster-1.jpg"},
            {"@type": "ImageObject", "url": "https://static-o.tickpick.com/poster-2.jpg"},
        ],
    }
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.image_url == "https://static-o.tickpick.com/poster-1.jpg"


def test_parse_event_image_absent_is_none_and_does_not_raise():
    # This is the real live shape as of 2026-07-21 — every Chapel of Bones
    # event currently omits `image` entirely.
    parsed = _scraper()._parse_event(dict(_BASE_EVENT))
    assert parsed is not None
    assert parsed.image_url is None


def test_parse_event_empty_image_list_is_none():
    data = {**_BASE_EVENT, "image": []}
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.image_url is None


def test_parse_event_blank_image_string_is_none():
    # An empty or whitespace-only `image` must not persist as a broken <img src>;
    # it normalizes to None like every other scraper's `... or None`.
    for blank in ("", "   ", "\n\t"):
        data = {**_BASE_EVENT, "image": blank}
        parsed = _scraper()._parse_event(data)
        assert parsed is not None
        assert parsed.image_url is None
