"""Manager-level test for the Nectar Lounge scraper (issue #69).

Exercises the real ScrapeManager -> registry -> NectarScraper -> DB upsert path
end to end, against the real saved fixture (tests/fixtures/nectar_lounge.html)
with only the HTTP fetch stubbed out. Builds its own Venue row directly (rather
than the generic `make_venue` factory) so the venue's `scraper_type`/
`scraper_config` are exactly what a real Nectar Lounge seed row would carry.
"""

from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import select

from app.models import Event, Venue
from app.scrapers.nectar import NectarScraper
from app.scrapers.manager import ScrapeManager

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nectar_lounge.html"


async def test_manager_scrapes_nectar_and_upserts_only_nectar_events(session, monkeypatch):
    # This test's own Venue, built directly (not via the generic make_venue
    # factory) with the scraper_type/scraper_config a real Nectar Lounge seed
    # row would carry.
    venue = Venue(
        name="Nectar Lounge",
        slug="nectar",
        city="Seattle",
        size_category="medium",
        scraper_type="nectar",
        scraper_config={"url": "https://www.nectarlounge.com/"},
        color="#6a1b9a",
    )
    session.add(venue)
    await session.commit()

    async def _fake_fetch_soup(self, url, **kwargs):
        return BeautifulSoup(_FIXTURE_PATH.read_text(), "lxml")

    monkeypatch.setattr(NectarScraper, "fetch_soup", _fake_fetch_soup)

    manager = ScrapeManager(session)
    scraper = manager._get_scraper(venue)
    assert isinstance(scraper, NectarScraper)

    scraped = await scraper.scrape()
    created, updated = await manager._upsert_events(venue.id, scraped)
    await session.commit()

    # Fixture carries 10 raw JSON-LD Events, only 6 of which are Nectar's own
    # (the other 4 are Hidden Hall — see nectar.py's module docstring).
    assert (created, updated) == (6, 0)

    events = (
        await session.execute(select(Event).where(Event.venue_id == venue.id))
    ).scalars().all()
    assert {e.name for e in events} == {
        "SOUR TIMES - a tribute to Portishead",
        '"TiK ToK 2010s Recession Pop: Party Rock Heatwave" feat DJ Dance Dance',
        "JIMOTHY RAVE",
        "Mo' Jam Mondays",
        "Tyler McGinnis with Izzy Burns, Ray Baron, The Orphan 40",
        "BIG BRASS EXTRAVAGANZA #14 feat:  This Much Brass, Chaotic Noise Marching Corps, AGAB, 8-Bit Brass Band",
    }

    # The trailing numeric Tixr id must be the identity-bearing source_key
    # (ext: tier) — the URL_IDENTITY=HASH_FALLBACK audit in nectar.py means
    # source_url text is never trusted for identity, only the numeric id is.
    sour_times = next(e for e in events if e.name.startswith("SOUR TIMES"))
    assert sour_times.source_key == "ext:195530"
