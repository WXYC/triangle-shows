"""Nectar Lounge (Seattle) scraper: parse-layer behavior (issue #69).

Pure unit tests fed a real saved fixture (tests/fixtures/nectar_lounge.html —
the single `<script type="application/ld+json">` block verbatim from a live
`curl` of https://www.nectarlounge.com/ on 2026-07-24, wrapped in a minimal HTML
skeleton) — no database, no HTTP. The fixture's 10-event array is real
nectarlounge.com output: 6 carry `location.name == "Nectar Lounge"`, and 4 carry
`location.name == "Hidden Hall"` (hiddenhall.com currently aliases Nectar's own
calendar — see nectar.py's module docstring and issue #69) which this scraper
must filter out entirely.
"""

from datetime import date, time
from pathlib import Path

from bs4 import BeautifulSoup

from app.scrapers.nectar import NectarScraper

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nectar_lounge.html"


def _scraper() -> NectarScraper:
    return NectarScraper("nectar", {"url": "https://www.nectarlounge.com/"})


def _soup() -> BeautifulSoup:
    return BeautifulSoup(_FIXTURE_PATH.read_text(), "lxml")


async def test_scrape_end_to_end_on_fixture_returns_only_nectar_events(monkeypatch):
    scraper = _scraper()

    async def _fake_fetch_soup(self, url, **kwargs):
        return _soup()

    monkeypatch.setattr(NectarScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    names = {e.name for e in events}
    assert names == {
        "SOUR TIMES - a tribute to Portishead",
        '"TiK ToK 2010s Recession Pop: Party Rock Heatwave" feat DJ Dance Dance',
        "JIMOTHY RAVE",
        "Mo' Jam Mondays",
        "Tyler McGinnis with Izzy Burns, Ray Baron, The Orphan 40",
        "BIG BRASS EXTRAVAGANZA #14 feat:  This Much Brass, Chaotic Noise Marching Corps, AGAB, 8-Bit Brass Band",
    }
    # The fixture's 4 Hidden Hall events must never surface as Nectar events.
    assert "KHU.ÉEX'" not in names
    assert not any("Hidden Hall" in n or "SHAFTY" in n for n in names)
    assert all(e.venue_slug == "nectar" for e in events)
    assert all(e.source == "nectar" for e in events)


async def test_scrape_converts_utc_startdate_to_seattle_local_date_and_time(monkeypatch):
    # "Mo' Jam Mondays" own description text says "7.27 Monday" while its
    # startDate is "2026-07-28T02:30:00+00:00" (a Tuesday read literally as
    # UTC) — the scraper must convert to Seattle local time, not just read the
    # UTC date/time components directly (see nectar.py's _SEATTLE_TZ comment).
    scraper = _scraper()

    async def _fake_fetch_soup(self, url, **kwargs):
        return _soup()

    monkeypatch.setattr(NectarScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    mo_jam = next(e for e in events if e.name == "Mo' Jam Mondays")
    assert mo_jam.date == date(2026, 7, 27)
    assert mo_jam.show_time == time(19, 30)


async def test_scrape_extracts_trailing_numeric_tixr_id_as_external_id(monkeypatch):
    scraper = _scraper()

    async def _fake_fetch_soup(self, url, **kwargs):
        return _soup()

    monkeypatch.setattr(NectarScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    sour_times = next(e for e in events if e.name == "SOUR TIMES - a tribute to Portishead")
    assert sour_times.external_id == "195530"
    assert sour_times.source_url == (
        "https://www.tixr.com/groups/nectarlounge/events/sour-times-a-tribute-to-portishead-195530"
    )
    assert sour_times.ticket_url == sour_times.source_url


async def test_scrape_unescapes_html_entities_and_stray_backslash_escapes(monkeypatch):
    # The WordPress plugin double-encodes: raw title text goes through esc_js()
    # AND is placed inside JSON, so "Portland's" round-trips as a literal
    # backslash-apostrophe once json.loads has already collapsed the doubled
    # backslash the raw HTML carries (see nectar.py's _clean_text docstring).
    scraper = _scraper()

    async def _fake_fetch_soup(self, url, **kwargs):
        return _soup()

    monkeypatch.setattr(NectarScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    mo_jam = next(e for e in events if e.name == "Mo' Jam Mondays")
    assert "\\" not in mo_jam.name
    assert mo_jam.name == "Mo' Jam Mondays"

    tik_tok = next(
        e for e in events
        if e.name.startswith('"TiK ToK')
    )
    assert "&quot;" not in tik_tok.name
    assert "&#034;" not in tik_tok.name


async def test_scrape_extracts_image_and_description(monkeypatch):
    scraper = _scraper()

    async def _fake_fetch_soup(self, url, **kwargs):
        return _soup()

    monkeypatch.setattr(NectarScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    sour_times = next(e for e in events if e.name == "SOUR TIMES - a tribute to Portishead")
    assert sour_times.image_url == (
        "https://static.tixr.com/static/images/external/img/49849596-e991-4850-8a7f-e054fd698dc2.jpg"
    )
    # The double-HTML-encoded description ("&lt;p&gt;...") must come out as real
    # markup nh3 recognized and kept (a <p>/<strong> survived sanitization),
    # not as literal visible "<p>" text — proof _clean_text's html.unescape()
    # ran before ScrapedEvent.__post_init__'s clean_description() sanitized it.
    assert sour_times.description is not None
    assert "<p" in sour_times.description
    assert "<strong>SOUR TIMES" in sour_times.description
    assert "&lt;" not in sour_times.description


async def test_no_jsonld_script_yields_no_events(monkeypatch):
    scraper = _scraper()

    async def _fake_fetch_soup(self, url, **kwargs):
        return BeautifulSoup("<html><body>no events here</body></html>", "lxml")

    monkeypatch.setattr(NectarScraper, "fetch_soup", _fake_fetch_soup)

    assert await scraper.scrape() == []
