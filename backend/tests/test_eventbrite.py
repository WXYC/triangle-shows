"""Eventbrite scraper (Substation, The Vera Project): parse-layer behavior.

Pure unit tests — ``_parse_jsonld_event``/``_extract_event_urls`` are fed data
shaped like the live pages, so there is no database and no HTTP. The scraper
fetches via the shared BaseScraper.fetch_soup path; see tests/test_fetch_soup.py
for that shared behavior.

Fixtures below are trimmed from real captures (2026-07-24):
  - The per-event JSON-LD is a verbatim ``<script type="application/ld+json">``
    block from https://www.eventbrite.com/e/deep-dish-tickets-1990818944053
    (Substation), with the real WXYC-representative act name swapped in for
    the touring DJ act that was actually booked, so the fixture doesn't read
    as promoting a specific real show.
  - The ``__NEXT_DATA__`` listing shape is trimmed from
    https://www.eventbrite.com/o/substation-18831550522, which listed 12
    upcoming events at capture time — trimmed here to 2 for readability, with
    the same live ``props.pageProps.upcomingEvents[].url``/``name`` keys.
    The Vera Project's own organizer page (18177013613) had zero upcoming
    Eventbrite events at capture time, confirming the "empty listing" path
    against production rather than a construction.
"""

from datetime import date, time

from app.scrapers.eventbrite import EventbriteScraper

# Verbatim shape of a live Substation event's JSON-LD (captured 2026-07-24 from
# https://www.eventbrite.com/e/deep-dish-tickets-1990818944053), with the act
# name swapped for a WXYC-representative artist.
_BASE_EVENT = {
    "@context": "https://schema.org",
    "@type": "Event",
    "name": "Chuquimamani-Condori",
    "description": "Chuquimamani-Condori at Substation Seattle",
    "url": "https://www.eventbrite.com/e/chuquimamani-condori-tickets-1990818944053",
    "image": (
        "https://img.evbuc.com/https%3A%2F%2Fcdn.evbuc.com%2Fimages%2F1186618117"
        "%2F2801720828791%2F1%2Foriginal.20260610-184516"
        "?crop=focalpoint&fit=crop&w=940&auto=format%2Ccompress&q=75&sharp=10&fp-x=0.5&fp-y=0.5"
    ),
    "eventStatus": "https://schema.org/EventScheduled",
    "location": {
        "@type": "Place",
        "name": "Substation",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Seattle",
            "addressRegion": "WA",
            "addressCountry": "US",
            "streetAddress": "645 NW 45th St, Seattle, WA 98107",
        },
    },
    "organizer": {
        "@type": "Organization",
        "name": "Substation",
        "url": "https://www.eventbrite.com/o/substation-18831550522",
        "description": "Warehouse music venue featuring live bands and electronic music.",
    },
    "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
    "inLanguage": "en-US",
    "startDate": "2026-07-24T22:00:00-07:00",
    "performer": [
        {"@type": "Person", "name": "Chuquimamani-Condori"},
        {"@type": "Person", "name": "Jessica Pratt"},
    ],
    "offers": [
        {
            "@type": "AggregateOffer",
            "lowPrice": "44.08",
            "highPrice": "44.08",
            "url": "https://www.eventbrite.com/e/chuquimamani-condori-tickets-1990818944053",
            "availability": "InStock",
            "availabilityStarts": "2026-07-24T08:00:00Z",
            "availabilityEnds": "2026-07-26T05:00:00Z",
            "validFrom": "2026-07-24T08:00:00Z",
            "priceCurrency": "USD",
        }
    ],
}


def _scraper() -> EventbriteScraper:
    return EventbriteScraper("substation", {"organizer_id": "substation-18831550522"})


# --- Per-event JSON-LD parsing ---


def test_parses_real_substation_event_shape():
    parsed = _scraper()._parse_jsonld_event(dict(_BASE_EVENT))
    assert parsed is not None
    assert parsed.name == "Chuquimamani-Condori"
    assert parsed.date == date(2026, 7, 24)
    assert parsed.show_time == time(22, 0)
    assert parsed.venue_slug == "substation"
    assert parsed.source == "eventbrite"
    assert parsed.headliner == "Chuquimamani-Condori"
    assert parsed.support_artists == ["Jessica Pratt"]
    assert parsed.artist == "Chuquimamani-Condori"
    assert parsed.price_min == 44.08
    assert parsed.price_max == 44.08
    assert parsed.status == "on_sale"
    assert parsed.image_url == _BASE_EVENT["image"]
    assert parsed.ticket_url == _BASE_EVENT["url"]
    assert parsed.source_url == _BASE_EVENT["url"]
    # The trailing numeric segment of the URL is the durable per-event id —
    # this scraper's actual identity anchor (URL_IDENTITY is HASH_FALLBACK).
    assert parsed.external_id == "1990818944053"


def test_no_performer_falls_back_to_event_name_as_artist():
    data = {**_BASE_EVENT, "performer": []}
    parsed = _scraper()._parse_jsonld_event(data)
    assert parsed is not None
    assert parsed.headliner is None
    assert parsed.support_artists == []
    assert parsed.artist == "Chuquimamani-Condori"


def test_cancelled_event_status_maps_to_cancelled():
    data = {**_BASE_EVENT, "eventStatus": "https://schema.org/EventCancelled"}
    parsed = _scraper()._parse_jsonld_event(data)
    assert parsed is not None
    assert parsed.status == "cancelled"


def test_zero_price_offer_maps_to_free_status():
    data = {
        **_BASE_EVENT,
        "offers": [{**_BASE_EVENT["offers"][0], "lowPrice": "0", "highPrice": "0"}],
    }
    parsed = _scraper()._parse_jsonld_event(data)
    assert parsed is not None
    assert parsed.price_min == 0.0
    assert parsed.status == "free"


def test_missing_offers_yields_no_price_and_on_sale_status():
    data = {k: v for k, v in _BASE_EVENT.items() if k != "offers"}
    parsed = _scraper()._parse_jsonld_event(data)
    assert parsed is not None
    assert parsed.price_min is None
    assert parsed.price_max is None
    assert parsed.status == "on_sale"


def test_missing_name_returns_none():
    data = {k: v for k, v in _BASE_EVENT.items() if k != "name"}
    assert _scraper()._parse_jsonld_event(data) is None


def test_missing_start_date_returns_none():
    data = {k: v for k, v in _BASE_EVENT.items() if k != "startDate"}
    assert _scraper()._parse_jsonld_event(data) is None


def test_external_id_absent_when_url_missing():
    data = {k: v for k, v in _BASE_EVENT.items() if k != "url"}
    parsed = _scraper()._parse_jsonld_event(data)
    assert parsed is not None
    assert parsed.external_id is None
    assert parsed.source_url is None


# --- _parse_event_page: schema.org Event picked out among multiple JSON-LD scripts ---


def test_parse_event_page_skips_webpage_block_and_finds_event():
    from bs4 import BeautifulSoup
    import json

    # Real Eventbrite detail pages emit a WebPage JSON-LD block ahead of the
    # Event block (captured 2026-07-24) — the parser must not stop at the first
    # <script type="application/ld+json"> it sees.
    webpage_block = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "Chuquimamani-Condori",
        "url": _BASE_EVENT["url"],
    }
    html = (
        f'<script type="application/ld+json">{json.dumps(webpage_block)}</script>'
        f'<script type="application/ld+json">{json.dumps(_BASE_EVENT)}</script>'
    )
    soup = BeautifulSoup(html, "lxml")
    parsed = _scraper()._parse_event_page(soup)
    assert parsed is not None
    assert parsed.name == "Chuquimamani-Condori"


# --- _extract_event_urls: link discovery from the listing's __NEXT_DATA__ blob ---
# Shape trimmed from https://www.eventbrite.com/o/substation-18831550522
# (captured 2026-07-24; keys present on that page's real upcomingEvents items).


def _next_data_html(upcoming_events: list[dict]) -> "BeautifulSoup":
    import json as _json

    from bs4 import BeautifulSoup

    blob = {"props": {"pageProps": {"upcomingEvents": upcoming_events}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{_json.dumps(blob)}</script>'
    return BeautifulSoup(html, "lxml")


def test_extract_event_urls_from_real_shape():
    soup = _next_data_html(
        [
            {
                "id": "1990818944053",
                "name": "Deep Dish",
                "url": "https://www.eventbrite.com/e/deep-dish-tickets-1990818944053",
                "start_date": "2026-07-24",
            },
            {
                "id": "1988945512569",
                "name": "MUST DIE!",
                "url": "https://www.eventbrite.com/e/must-die-tickets-1988945512569",
                "start_date": "2026-07-25",
            },
        ]
    )
    urls = _scraper()._extract_event_urls(soup)
    assert urls == [
        "https://www.eventbrite.com/e/deep-dish-tickets-1990818944053",
        "https://www.eventbrite.com/e/must-die-tickets-1988945512569",
    ]


def test_extract_event_urls_dedupes():
    same = "https://www.eventbrite.com/e/deep-dish-tickets-1990818944053"
    soup = _next_data_html([{"url": same}, {"url": same}])
    assert _scraper()._extract_event_urls(soup) == [same]


def test_extract_event_urls_empty_upcoming_list_is_real_vera_project_shape():
    # The Vera Project's own organizer page (18177013613) had zero upcoming
    # Eventbrite events at capture time — confirmed against production, not
    # constructed. Must degrade to an empty list, not raise.
    soup = _next_data_html([])
    assert _scraper()._extract_event_urls(soup) == []


def test_extract_event_urls_missing_next_data_returns_empty():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<html><body>no data here</body></html>", "lxml")
    assert _scraper()._extract_event_urls(soup) == []


def test_extract_event_urls_malformed_json_returns_empty():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        '<script id="__NEXT_DATA__" type="application/json">{not json</script>', "lxml"
    )
    assert _scraper()._extract_event_urls(soup) == []


def test_scrape_raises_without_organizer_id():
    import asyncio

    import pytest

    scraper = EventbriteScraper("substation", {})
    with pytest.raises(ValueError):
        asyncio.run(scraper.scrape())


# --- Manager-level: dispatch through the registry, full two-hop fetch, real upsert ---
#
# Own Venue (scraper_type="eventbrite"), real PostgreSQL via the conftest
# factories. Only the HTTP layer (fetch_soup) is stubbed — everything from
# ScrapeManager.scrape_venue dispatching to "eventbrite" through
# EventbriteScraper's own listing-parse, detail-fetch, and JSON-LD parse, to
# the upsert's source_key derivation runs for real.


async def test_manager_scrapes_eventbrite_venue_end_to_end(session, make_venue, monkeypatch):
    import json as _json

    from bs4 import BeautifulSoup

    from app.models import Event
    from app.scrapers.eventbrite import EventbriteScraper
    from app.scrapers.manager import ScrapeManager
    from sqlalchemy import select

    listing_url = "https://www.eventbrite.com/o/substation-18831550522"
    detail_url = "https://www.eventbrite.com/e/chuquimamani-condori-tickets-1990818944053"

    next_data_blob = {
        "props": {
            "pageProps": {
                "upcomingEvents": [{"id": "1990818944053", "name": "Chuquimamani-Condori", "url": detail_url}]
            }
        }
    }
    listing_html = f'<script id="__NEXT_DATA__" type="application/json">{_json.dumps(next_data_blob)}</script>'
    detail_html = f'<script type="application/ld+json">{_json.dumps(_BASE_EVENT)}</script>'

    async def fake_fetch_soup(self, url, *, client=None, headers=None, parser="lxml"):
        html = listing_html if url == listing_url else detail_html
        return BeautifulSoup(html, "lxml")

    monkeypatch.setattr(EventbriteScraper, "fetch_soup", fake_fetch_soup)

    venue = await make_venue(
        scraper_type="eventbrite",
        scraper_config={"organizer_id": "substation-18831550522"},
    )

    result = await ScrapeManager(session).scrape_venue(venue)

    assert result["status"] == "success"
    assert (result["found"], result["created"], result["updated"]) == (1, 1, 0)

    event = (await session.execute(select(Event).where(Event.venue_id == venue.id))).scalar_one()
    assert event.name == "Chuquimamani-Condori"
    assert event.date == date(2026, 7, 24)
    assert event.external_id == "1990818944053"
    # ext: tier wins regardless of this scraper's HASH_FALLBACK URL_IDENTITY
    # verdict (derive_source_key checks external_id before the verdict-gated
    # url: tier) — the whole point of extracting it in _extract_external_id.
    assert event.source_key == "ext:1990818944053"
