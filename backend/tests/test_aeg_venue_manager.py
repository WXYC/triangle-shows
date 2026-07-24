"""Manager-level tests for the AEG venue-site scraper (issue #68).

Exercises the real ScrapeManager -> registry -> AEGVenueScraper -> DB upsert
path end to end, against the real saved fixtures (tests/fixtures/
showboxpresents_events.html, tests/fixtures/neumos_events.html) with only the
HTTP fetch stubbed out. Builds its own Venue rows directly (rather than the
generic `make_venue` factory) so each venue's `scraper_type`/`scraper_config`
is exactly what a real AEG venue seed row would carry (see #71 for the actual
seed wiring, out of scope here).
"""

from datetime import date, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import select

from app.models import Event, Venue
from app.scrapers.aeg_venue import AEGVenueScraper
from app.scrapers.manager import ScrapeManager

_SHOWBOX_FIXTURE = Path(__file__).parent / "fixtures" / "showboxpresents_events.html"
_NEUMOS_FIXTURE = Path(__file__).parent / "fixtures" / "neumos_events.html"


def _future_showbox_html() -> str:
    d1 = (date.today() + timedelta(days=30)).strftime("%a, %b %d, %Y")
    d2 = (date.today() + timedelta(days=45)).strftime("%a, %b %d, %Y")
    html = _SHOWBOX_FIXTURE.read_text()
    html = html.replace("Fri, Jul 24, 2026", d1)
    html = html.replace("Sat, Aug 8, 2026", d2)
    return html


def _future_neumos_html() -> str:
    html = _NEUMOS_FIXTURE.read_text()
    base = date.today() + timedelta(days=30)
    replacements = [
        ("July 24 2026", (base + timedelta(days=0)).strftime("%B %d %Y")),
        ("July 25 2026", (base + timedelta(days=1)).strftime("%B %d %Y")),
        ("July 28 2026", (base + timedelta(days=4)).strftime("%B %d %Y")),
        ("July 29 2026", (base + timedelta(days=5)).strftime("%B %d %Y")),
        ("July 30 2026", (base + timedelta(days=6)).strftime("%B %d %Y")),
        ("July 31 2026", (base + timedelta(days=7)).strftime("%B %d %Y")),
        ("August  1 2026", (base + timedelta(days=8)).strftime("%B %d %Y")),
        ("August  5 2026", (base + timedelta(days=12)).strftime("%B %d %Y")),
        ("August  6 2026", (base + timedelta(days=13)).strftime("%B %d %Y")),
        ("August 11 2026", (base + timedelta(days=18)).strftime("%B %d %Y")),
        ("August 12 2026", (base + timedelta(days=19)).strftime("%B %d %Y")),
        ("August 14 2026", (base + timedelta(days=21)).strftime("%B %d %Y")),
    ]
    for old, new in replacements:
        html = html.replace(f'aria-label="{old}"', f'aria-label="{new}"')
    return html


async def test_manager_scrapes_showbox_and_upserts_only_its_own_venue(session, monkeypatch):
    venue = Venue(
        name="The Showbox",
        slug="showbox",
        city="Seattle",
        size_category="medium",
        scraper_type="aeg_venue",
        scraper_config={
            "url": "https://www.showboxpresents.com/events/all",
            "venue_name": "The Showbox",
        },
        color="#c62828",
    )
    session.add(venue)
    await session.commit()

    async def _fake_fetch_soup(self, url, **kwargs):
        return BeautifulSoup(_future_showbox_html(), "lxml")

    monkeypatch.setattr(AEGVenueScraper, "fetch_soup", _fake_fetch_soup)

    manager = ScrapeManager(session)
    scraper = manager._get_scraper(venue)
    assert isinstance(scraper, AEGVenueScraper)

    scraped = await scraper.scrape()
    created, updated = await manager._upsert_events(venue.id, scraped)
    await session.commit()

    # Only "Earlybirds Club" is both a Showbox-room card and in the future
    # ("Alabama Shakes" is a different venue; "Riot Ten"'s date is "TBD").
    assert (created, updated) == (1, 0)

    events = (await session.execute(select(Event).where(Event.venue_id == venue.id))).scalars().all()
    assert {e.name for e in events} == {"Earlybirds Club"}

    earlybirds = events[0]
    # No source_key assertion beyond presence: URL_IDENTITY=HASH_FALLBACK, so
    # identity comes from the ext: tier (the numeric AXS event id) whenever a
    # ticket link is present, per derive_source_key's ext > url > hash order.
    assert earlybirds.source_key == "ext:1464405"


async def test_manager_scrapes_showbox_sodo_as_a_separate_venue(session, monkeypatch):
    venue = Venue(
        name="Showbox SoDo",
        slug="showbox-sodo",
        city="Seattle",
        size_category="large",
        scraper_type="aeg_venue",
        scraper_config={
            "url": "https://www.showboxpresents.com/events/all",
            "venue_name": "Showbox SoDo",
        },
        color="#6a1b9a",
    )
    session.add(venue)
    await session.commit()

    async def _fake_fetch_soup(self, url, **kwargs):
        return BeautifulSoup(_future_showbox_html(), "lxml")

    monkeypatch.setattr(AEGVenueScraper, "fetch_soup", _fake_fetch_soup)

    manager = ScrapeManager(session)
    scraper = manager._get_scraper(venue)
    scraped = await scraper.scrape()
    created, updated = await manager._upsert_events(venue.id, scraped)
    await session.commit()

    assert (created, updated) == (2, 0)

    events = (await session.execute(select(Event).where(Event.venue_id == venue.id))).scalars().all()
    assert {e.name for e in events} == {
        "Dillstradamus (Dillon Francis B2B Flosstradamus)",
        "ISOxo presents: Hardcore Diva",
    }


async def test_manager_scrapes_neumos_and_upserts_all_skin_neumos_listings(session, monkeypatch):
    venue = Venue(
        name="Neumos",
        slug="neumos",
        city="Seattle",
        size_category="medium",
        scraper_type="aeg_venue",
        scraper_config={"url": "https://www.neumos.com/events", "skin": "neumos"},
        color="#37474f",
    )
    session.add(venue)
    await session.commit()

    async def _fake_fetch_soup(self, url, **kwargs):
        return BeautifulSoup(_future_neumos_html(), "lxml")

    monkeypatch.setattr(AEGVenueScraper, "fetch_soup", _fake_fetch_soup)

    manager = ScrapeManager(session)
    scraper = manager._get_scraper(venue)
    assert isinstance(scraper, AEGVenueScraper)

    scraped = await scraper.scrape()
    created, updated = await manager._upsert_events(venue.id, scraped)
    await session.commit()

    assert (created, updated) == (12, 0)

    events = (await session.execute(select(Event).where(Event.venue_id == venue.id))).scalars().all()
    assert len(events) == 12

    emo_nite = next(e for e in events if e.name == "Emo Nite")
    assert emo_nite.source_key == "ext:1498017"
