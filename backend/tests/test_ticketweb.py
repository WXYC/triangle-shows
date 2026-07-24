"""TicketWeb scraper (issue #67): parse-layer and manager-integration behavior.

The fixture HTML below is modeled on a real fetch of Tractor's live TicketWeb
venue page (https://www.ticketweb.com/venue/tractor-seattle-wa/18807), captured
via the Wayback Machine on 2026-07-21 after the live host rejected direct
scraping with an Akamai bot-block (HTTP 506). The captured page embeds a bare
JSON-LD array of schema.org ``MusicEvent`` objects plus a trailing
``EventVenue`` block — real key shapes (``@type``, ``name``, ``image``,
``url``, ``startDate``, ``performer``, ``offers``) and the real
double-slash-in-path image URL quirk and HTML-entity-encoded ``&amp;`` inside
JSON string values that TicketWeb's page emits — with WXYC-representative acts
swapped in for the event/performer names (see repo CLAUDE.md "Example Music
Data").

``_StubScraper``-style mocking (test_vanished_events.py) is not used for the
manager-level test below: this scraper's HTTP path (fetch_soup -> JSON-LD
parse) is exactly what issue #67 needs covered, so the manager test drives it
through an httpx.MockTransport substituted for BaseScraper.http_client rather
than stubbing scrape() itself.
"""

import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import Event
from app.scrapers.base import BaseScraper
from app.scrapers.manager import ScrapeManager
from app.scrapers.ticketweb import TicketWebScraper

_FUTURE_1 = (date.today() + timedelta(days=10)).isoformat()
_FUTURE_2 = (date.today() + timedelta(days=17)).isoformat()
_PAST = (date.today() - timedelta(days=5)).isoformat()

# Bare JSON-LD array as embedded on the live Tractor venue page (Wayback Machine
# snapshot 20260721181932, fetched 2026-07-21): one MusicEvent with a headliner +
# support performers and an image, one MusicEvent with a single performer and no
# image (schema.org's `image` is absent on some live listings), and a trailing
# `EventVenue` block that must be skipped (it is not an Event/MusicEvent).
_VENUE_EVENTS_JSON = f"""
[
{{
    "@context" : "http://schema.org",
    "@type" : "MusicEvent",
    "name" : "Hermanos Gutiérrez w/ Csillagrabólók &amp; Nilüfer Yanya",
    "image" : "https://www.ticketweb.com//i/00/13/36/25/43_Listings.jpg?v=10",
    "url" : "https://www.ticketweb.com/event/hermanos-gutierrez-w-tractor-tickets/14751933",
    "startDate" : "{_FUTURE_1}T20:00",
    "eventStatus" : "",
    "location" : {{
        "@type" : "Place",
        "name" : "Tractor",
        "sameAs" : "https://www.ticketweb.com/venue/Tractor/18807",
        "address" : {{
            "@type" : "PostalAddress",
            "streetAddress" : "5213 Ballard Avenue NW",
            "addressLocality" : "Seattle",
            "addressRegion" : "WA",
            "postalCode" : "98107",
            "addressCountry" : "US"
        }}
    }},
    "performer" : [
        {{ "name" : "Hermanos Gutiérrez" }},
        {{ "name" : "Csillagrabólók" }},
        {{ "name" : "Nilüfer Yanya" }}
    ],
    "offers": {{
        "@type": "Offer",
        "url": "https://www.ticketweb.com/event/hermanos-gutierrez-w-tractor-tickets/14751933",
        "availability": "InStock"
    }}
}},
{{
    "@context" : "http://schema.org",
    "@type" : "MusicEvent",
    "name" : "Chuquimamani-Condori",
    "url" : "https://www.ticketweb.com/event/chuquimamani-condori-tractor-tickets/14241894",
    "startDate" : "{_FUTURE_2}T19:30",
    "eventStatus" : "",
    "location" : {{
        "@type" : "Place",
        "name" : "Tractor",
        "sameAs" : "https://www.ticketweb.com/venue/Tractor/18807"
    }},
    "performer" : [
        {{ "name" : "Chuquimamani-Condori" }}
    ],
    "offers": {{
        "@type": "Offer",
        "url": "https://www.ticketweb.com/event/chuquimamani-condori-tractor-tickets/14241894",
        "availability": "InStock"
    }}
}},
{{
    "@context" : "http://schema.org",
    "@type" : "MusicEvent",
    "name" : "Duke Ellington & John Coltrane",
    "url" : "https://www.ticketweb.com/event/duke-ellington-coltrane-tractor-tickets/14000001",
    "startDate" : "{_PAST}T20:00",
    "performer" : [
        {{ "name" : "Duke Ellington & John Coltrane" }}
    ]
}}
]
"""

_EVENT_VENUE_JSON = """
[
    {
        "@context" : "http://schema.org",
        "@type" : "EventVenue",
        "name" : "Tractor",
        "sameAs" : "https://www.ticketweb.com/venue/Tractor/18807",
        "address" : {
            "@type" : "PostalAddress",
            "streetAddress" : "5213 Ballard Avenue NW",
            "addressLocality" : "Seattle",
            "addressRegion" : "WA",
            "postalCode" : "98107",
            "addressCountry" : "US"
        }
    }
]
"""

_VENUE_HTML = f"""
<html><head><title>Tractor Seattle, WA Tickets</title></head>
<body>
<script type="application/ld+json">{_VENUE_EVENTS_JSON}</script>
<script type="application/ld+json">{_EVENT_VENUE_JSON}</script>
</body></html>
"""


def _scraper() -> TicketWebScraper:
    return TicketWebScraper(
        "tractor-tavern", {"ticketweb_slug": "tractor-seattle-wa", "ticketweb_id": "18807"}
    )


# --- Config validation ---


async def test_scrape_raises_without_config():
    with pytest.raises(ValueError):
        await TicketWebScraper("tractor-tavern", {}).scrape()


# --- Parse-layer: fed real-shaped JSON-LD dicts directly ---


def _event_dict(**overrides) -> dict:
    base = {
        "@type": "MusicEvent",
        "name": "Hermanos Gutiérrez",
        "url": "https://www.ticketweb.com/event/hermanos-gutierrez-tractor-tickets/14751933",
        "startDate": f"{_FUTURE_1}T20:00",
        "performer": [{"name": "Hermanos Gutiérrez"}],
    }
    base.update(overrides)
    return base


def test_parse_event_extracts_headliner_and_support():
    data = _event_dict(
        performer=[
            {"name": "Hermanos Gutiérrez"},
            {"name": "Csillagrabólók"},
            {"name": "Nilüfer Yanya"},
        ]
    )
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.headliner == "Hermanos Gutiérrez"
    assert parsed.support_artists == ["Csillagrabólók", "Nilüfer Yanya"]


def test_parse_event_single_performer_has_no_support():
    parsed = _scraper()._parse_event(_event_dict())
    assert parsed is not None
    assert parsed.headliner == "Hermanos Gutiérrez"
    assert parsed.support_artists == []


def test_parse_event_extracts_string_image():
    data = _event_dict(image="https://www.ticketweb.com//i/00/13/36/25/43_Listings.jpg?v=10")
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.image_url == "https://www.ticketweb.com//i/00/13/36/25/43_Listings.jpg?v=10"


def test_parse_event_image_absent_is_none():
    parsed = _scraper()._parse_event(_event_dict())
    assert parsed is not None
    assert parsed.image_url is None


def test_parse_event_unescapes_html_entities_in_name():
    data = _event_dict(name="Csillagrabólók &amp; Nilüfer Yanya")
    parsed = _scraper()._parse_event(data)
    assert parsed is not None
    assert parsed.name == "Csillagrabólók & Nilüfer Yanya"


def test_parse_event_extracts_external_id_from_url():
    parsed = _scraper()._parse_event(_event_dict())
    assert parsed is not None
    assert parsed.external_id == "14751933"


def test_parse_event_source_url_is_per_event_ticket_url():
    parsed = _scraper()._parse_event(_event_dict())
    assert parsed is not None
    assert parsed.source_url == "https://www.ticketweb.com/event/hermanos-gutierrez-tractor-tickets/14751933"
    assert parsed.source == "ticketweb"


def test_parse_event_rejects_missing_name():
    data = _event_dict()
    data["name"] = ""
    assert _scraper()._parse_event(data) is None


def test_parse_event_rejects_missing_start_date():
    data = _event_dict()
    del data["startDate"]
    assert _scraper()._parse_event(data) is None


def test_parse_event_drops_past_events():
    data = _event_dict(startDate=f"{_PAST}T20:00")
    assert _scraper()._parse_event(data) is None


# --- scrape(): full JSON-LD walk over a fetched page ---


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_scrape_parses_events_and_skips_non_event_blocks(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/venue/tractor-seattle-wa/18807"
        return httpx.Response(200, text=_VENUE_HTML)

    monkeypatch.setattr(BaseScraper, "http_client", staticmethod(lambda headers=None: _mock_client(handler)))

    events = await _scraper().scrape()

    # 3 raw MusicEvent entries in the fixture, one in the past -> 2 survive;
    # the EventVenue block in the second script tag contributes nothing.
    assert len(events) == 2
    names = {e.name for e in events}
    assert "Hermanos Gutiérrez w/ Csillagrabólók & Nilüfer Yanya" in names
    assert "Chuquimamani-Condori" in names


# --- Manager-level integration: builds its own in-test Venue (issue #67 scope) ---


async def test_ticketweb_scrapes_end_to_end_via_manager(session, make_venue, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_VENUE_HTML)

    monkeypatch.setattr(BaseScraper, "http_client", staticmethod(lambda headers=None: _mock_client(handler)))

    venue = await make_venue(
        name="Tractor Tavern",
        slug="tractor-tavern",
        scraper_type="ticketweb",
        scraper_config={"ticketweb_slug": "tractor-seattle-wa", "ticketweb_id": "18807"},
    )

    result = await ScrapeManager(session).scrape_venue(venue)
    assert result is not None

    rows = (await session.execute(select(Event).where(Event.venue_id == venue.id))).scalars().all()
    assert len(rows) == 2
    by_name = {e.name: e for e in rows}
    hermanos = by_name["Hermanos Gutiérrez w/ Csillagrabólók & Nilüfer Yanya"]
    assert hermanos.headliner == "Hermanos Gutiérrez"
    assert hermanos.image_url == "https://www.ticketweb.com//i/00/13/36/25/43_Listings.jpg?v=10"
    assert hermanos.ticket_url == "https://www.ticketweb.com/event/hermanos-gutierrez-w-tractor-tickets/14751933"
