"""Manager-level test for the Crocodile scraper (issue #75).

Exercises the real ScrapeManager -> registry -> CrocodileScraper -> DB upsert
path end to end, against the real saved fixture (tests/fixtures/
crocodile_calendar.html) with only the HTTP fetch stubbed out. Builds its own
Venue row directly (rather than the generic `make_venue` factory) so the venue's
`scraper_type`/`scraper_config` are exactly what a real Crocodile seed row would
carry.
"""

from datetime import date, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import select

from app.models import Event, Venue
from app.scrapers.crocodile import CrocodileScraper
from app.scrapers.manager import ScrapeManager

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "crocodile_calendar.html"


def _future_fixture_html() -> str:
    """Shift the fixture's internal-link dates into the future (see
    test_crocodile.py's `_future_fixture_soup` for why) so the manager's
    upcoming-events path has something to upsert regardless of when the suite runs."""
    d1 = (date.today() + timedelta(days=30)).strftime("%b %d, %Y")
    d2 = (date.today() + timedelta(days=60)).strftime("%b %d, %Y")
    html = _FIXTURE_PATH.read_text()
    html = html.replace("Jul 24, 2026 10:00 PM", f"{d1} 10:00 PM")
    html = html.replace("Jul 25, 2026 6:00 PM", f"{d1} 6:00 PM")
    html = html.replace("Jul 25, 2026 10:00 PM", f"{d1} 10:00 PM")
    html = html.replace("Sep 29, 2026 8:00 PM", f"{d2} 8:00 PM")
    return html


async def test_manager_scrapes_crocodile_and_upserts_all_listings(session, monkeypatch):
    # This test's own Venue, built directly (not via the generic make_venue
    # factory) with the scraper_type/scraper_config a real Crocodile seed row
    # would carry.
    venue = Venue(
        name="The Crocodile",
        slug="crocodile",
        city="Seattle",
        size_category="medium",
        scraper_type="crocodile",
        scraper_config={"url": "https://calendar.thecrocodile.com/"},
        color="#2e7d32",
    )
    session.add(venue)
    await session.commit()

    async def _fake_fetch_soup(self, url, **kwargs):
        return BeautifulSoup(_future_fixture_html(), "lxml")

    monkeypatch.setattr(CrocodileScraper, "fetch_soup", _fake_fetch_soup)

    manager = ScrapeManager(session)
    scraper = manager._get_scraper(venue)
    assert isinstance(scraper, CrocodileScraper)

    scraped = await scraper.scrape()
    created, updated = await manager._upsert_events(venue.id, scraped)
    await session.commit()

    assert (created, updated) == (4, 0)

    events = (
        await session.execute(select(Event).where(Event.venue_id == venue.id))
    ).scalars().all()
    assert {e.name for e in events} == {
        "Dimelo - A Latin Experience",
        "FULTON LEE: Sing With Me Tour 2026",
        "SOS: The Recession Pop Party",
        "Bella Kay: The Reckless Tour",
    }

    # The venue's own /shows/ detail page must be the identity-bearing
    # source_key (url: tier), never the outbound ticketer link — the
    # URL_IDENTITY=TRUSTED audit this scraper carries (see crocodile.py).
    fulton = next(e for e in events if e.name.startswith("FULTON LEE"))
    assert fulton.source_key == (
        "url:/shows/fulton-lee-sing-with-me-tour-2026-25-jul"
    )

    dimelo = next(e for e in events if e.name.startswith("Dimelo"))
    assert dimelo.source_key == "url:/shows/dimelo-a-latin-experience-25-jul"
