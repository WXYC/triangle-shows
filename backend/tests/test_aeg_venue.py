"""AEG venue-site scraper: parse-layer behavior (issue #68).

Pure unit tests fed real saved fixtures (tests/fixtures/showboxpresents_events.html,
tests/fixtures/neumos_events.html — see each file's header comment for exactly
what it covers) plus a handful of hand-built minimal cards for edge cases the
live snapshots didn't happen to carry (skin=barboza, a date with no time at
all) — no database, no HTTP.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.scrapers.aeg_venue import AEGVenueScraper

_SHOWBOX_FIXTURE = Path(__file__).parent / "fixtures" / "showboxpresents_events.html"
_NEUMOS_FIXTURE = Path(__file__).parent / "fixtures" / "neumos_events.html"


def _showbox_scraper(venue_name="The Showbox") -> AEGVenueScraper:
    return AEGVenueScraper(
        "showbox",
        {"url": "https://www.showboxpresents.com/events/all", "venue_name": venue_name},
    )


def _neumos_scraper(skin="neumos") -> AEGVenueScraper:
    return AEGVenueScraper("neumos", {"url": "https://www.neumos.com/events", "skin": skin})


def _future_showbox_html() -> str:
    """Shift the fixture's Showbox/SoDo dates into the future (mirrors
    test_crocodile.py's pattern) so the "skip past events" branch can't flip
    these to past events as the wall clock advances past the original fetch
    date. "Riot Ten"'s date is untouched — it's the literal string "TBD"."""
    d1 = (date.today() + timedelta(days=30)).strftime("%a, %b %d, %Y")
    d2 = (date.today() + timedelta(days=45)).strftime("%a, %b %d, %Y")
    html = _SHOWBOX_FIXTURE.read_text()
    html = html.replace("Fri, Jul 24, 2026", d1)
    html = html.replace("Sat, Aug 8, 2026", d2)
    return html


def _future_showbox_soup() -> BeautifulSoup:
    return BeautifulSoup(_future_showbox_html(), "lxml")


def _future_neumos_html() -> str:
    """Shift the fixture's dates into the future; every card shares
    "aria-label" values keyed on 2026 dates from the July 24 fetch."""
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


def _future_neumos_soup() -> BeautifulSoup:
    return BeautifulSoup(_future_neumos_html(), "lxml")


# --- Showbox-style fixture ---


def test_showbox_fixture_yields_five_cards():
    soup = BeautifulSoup(_SHOWBOX_FIXTURE.read_text(), "lxml")
    assert len(soup.select("div.entry")) == 5


def test_parse_item_matches_configured_venue_name():
    soup = _future_showbox_soup()
    item = next(
        i for i in soup.select("div.entry") if "Earlybirds Club" in i.select_one("h3 a").get_text()
    )
    parsed = _showbox_scraper("The Showbox")._parse_item(item, date.today(), "The Showbox", None)
    assert parsed is not None
    assert parsed.name == "Earlybirds Club"
    assert parsed.external_id == "1464405"
    assert parsed.ticket_url == (
        "https://www.axs.com/events/1464405/earlybirds-club-tickets"
        "?skin=showboxpresents&src=AEGLIVE_WSHBXSEA030115VEN001"
    )
    assert parsed.source_url == "https://www.showboxpresents.com/events/detail/1464405"
    assert (parsed.show_time.hour, parsed.show_time.minute) == (18, 0)


def test_parse_item_filters_out_non_showbox_venue_on_shared_page():
    # "Alabama Shakes" is on the same "/events/all" page but plays Dune
    # Peninsula, not a Showbox room — must be filtered out.
    soup = _future_showbox_soup()
    item = next(
        i for i in soup.select("div.entry") if "Alabama Shakes" in i.select_one("h3 a").get_text()
    )
    assert _showbox_scraper("The Showbox")._parse_item(item, date.today(), "The Showbox", None) is None


def test_parse_item_matches_showbox_sodo_as_a_distinct_venue():
    soup = _future_showbox_soup()
    item = next(
        i for i in soup.select("div.entry") if "Dillstradamus" in i.select_one("h3 a").get_text()
    )
    parsed = _showbox_scraper("Showbox SoDo")._parse_item(item, date.today(), "Showbox SoDo", None)
    assert parsed is not None
    assert parsed.external_id == "1305921"

    # The Showbox filter must NOT also match a SoDo-only card.
    assert _showbox_scraper("The Showbox")._parse_item(item, date.today(), "The Showbox", None) is None


def test_parse_item_tbd_date_is_unparseable_and_skipped():
    # "Riot Ten": postponed, date field literally reads "TBD" with no
    # span.time at all.
    soup = _future_showbox_soup()
    item = next(i for i in soup.select("div.entry") if "Riot Ten" in i.select_one("h3 a").get_text())
    assert _showbox_scraper("The Showbox")._parse_item(item, date.today(), "The Showbox", None) is None


def test_parse_item_image_url_from_thumb():
    soup = _future_showbox_soup()
    item = next(
        i for i in soup.select("div.entry") if "Earlybirds Club" in i.select_one("h3 a").get_text()
    )
    parsed = _showbox_scraper("The Showbox")._parse_item(item, date.today(), "The Showbox", None)
    assert parsed is not None
    assert parsed.image_url == (
        "https://images.discovery-prod.axs.com/2026/05/earlybirds-club-tickets_07-24-26_23_6a11fa88a216f.png"
    )


def test_parse_item_skips_past_events():
    html = """
    <div class="entry showboxpresents clearfix" data-state="WA">
      <div class="thumb"><a href="https://www.showboxpresents.com/events/detail/9"><img src="https://x/y.jpg"/></a></div>
      <div class="info">
        <h3 class="carousel_item_title_small"><a href="https://www.showboxpresents.com/events/detail/9">Old Show</a></h3>
        <div class="date-time-container">
          <span class="date">Fri, Jan 1, 2021</span>
          <span class="time">Show 7:00 PM</span>
          <span class="venue">@ The Showbox</span>
        </div>
      </div>
      <div class="buttons">
        <a class="tickets" href="https://www.axs.com/events/9/old-show-tickets?skin=showboxpresents">Buy Tickets</a>
      </div>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.entry")
    assert _showbox_scraper()._parse_item(item, date.today(), "The Showbox", None) is None


def test_parse_item_returns_none_without_a_ticket_link():
    html = """
    <div class="entry showboxpresents clearfix">
      <div class="thumb"><a href="https://www.showboxpresents.com/events/detail/9"><img src="https://x/y.jpg"/></a></div>
      <div class="info">
        <h3 class="carousel_item_title_small"><a href="https://www.showboxpresents.com/events/detail/9">No Ticket Yet</a></h3>
        <div class="date-time-container">
          <span class="date">Fri, Jan 1, 2099</span>
          <span class="venue">@ The Showbox</span>
        </div>
      </div>
      <div class="buttons"></div>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.entry")
    assert _showbox_scraper()._parse_item(item, date.today(), "The Showbox", None) is None


# --- Neumos-style fixture ---


def test_neumos_fixture_yields_twelve_cards():
    soup = BeautifulSoup(_NEUMOS_FIXTURE.read_text(), "lxml")
    assert len(soup.select("div.entry")) == 12


def test_parse_item_neumos_skin_matches():
    soup = _future_neumos_soup()
    item = next(i for i in soup.select("div.entry") if "Emo Nite" in i.select_one("h3 a").get_text())
    parsed = _neumos_scraper("neumos")._parse_item(item, date.today(), None, "neumos")
    assert parsed is not None
    assert parsed.name == "Emo Nite"
    assert parsed.external_id == "1498017"
    assert parsed.ticket_url == "https://www.axs.com/events/1498017/emo-nite-tickets?skin=neumos"
    assert parsed.source_url == "https://www.neumos.com/events/detail/emo-nite-tickets-1498017"
    assert (parsed.show_time.hour, parsed.show_time.minute) == (21, 0)


def test_parse_item_double_space_padded_day_parses():
    # "August  1 2026" — the site pads single-digit days with an extra space.
    soup = _future_neumos_soup()
    item = next(
        i for i in soup.select("div.entry")
        if "Charli XCX Night" in i.select_one("h3 a").get_text()
    )
    parsed = _neumos_scraper("neumos")._parse_item(item, date.today(), None, "neumos")
    assert parsed is not None


def test_parse_item_barboza_skin_filters_out_neumos_skin_card():
    soup = _future_neumos_soup()
    item = next(i for i in soup.select("div.entry") if "Emo Nite" in i.select_one("h3 a").get_text())
    assert _neumos_scraper("barboza")._parse_item(item, date.today(), None, "barboza") is None


def test_parse_item_barboza_skin_matches_a_barboza_card():
    # No skin=barboza card exists in the live 2026-07-24 snapshot (never
    # fabricate a "real" fixture row) — hand-built here to exercise the
    # filter's positive branch.
    future = (date.today() + timedelta(days=30)).strftime("%B %d %Y")
    html = f"""
    <div class="eventItem entry clearfix">
      <div class="thumb"><a href="https://www.neumos.com/events/detail/some-show-1999999"><img src="https://x/y.jpg"/></a></div>
      <div class="info clearfix">
        <h3 class="title"><a href="https://www.neumos.com/events/detail/some-show-1999999">Some Barboza Show</a></h3>
        <div aria-label="{future}" class="date neumos">
          <span class="m-date__singleDate"></span>
        </div>
        <div class="meta"><div class="time">Doors: 8:00 PM</div></div>
      </div>
      <div class="buttons">
        <a class="tickets" href="https://www.axs.com/events/1999999/some-show-tickets?skin=barboza">Buy Tickets</a>
      </div>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.entry")
    parsed = _neumos_scraper("barboza")._parse_item(item, date.today(), None, "barboza")
    assert parsed is not None
    assert parsed.name == "Some Barboza Show"
    assert parsed.external_id == "1999999"


def test_parse_item_date_present_no_time_leaves_show_time_none():
    future = (date.today() + timedelta(days=30)).strftime("%B %d %Y")
    html = f"""
    <div class="eventItem entry clearfix">
      <div class="thumb"><a href="https://www.neumos.com/events/detail/no-time-show-42"><img src="https://x/y.jpg"/></a></div>
      <div class="info clearfix">
        <h3 class="title"><a href="https://www.neumos.com/events/detail/no-time-show-42">No Time Show</a></h3>
        <div aria-label="{future}" class="date neumos">
          <span class="m-date__singleDate"></span>
        </div>
      </div>
      <div class="buttons">
        <a class="tickets" href="https://www.axs.com/events/42/no-time-show-tickets?skin=neumos">Buy Tickets</a>
      </div>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.entry")
    parsed = _neumos_scraper("neumos")._parse_item(item, date.today(), None, "neumos")
    assert parsed is not None
    assert parsed.show_time is None


def test_parse_item_returns_none_when_no_title():
    html = """
    <div class="eventItem entry clearfix">
      <div class="info clearfix">
        <div aria-label="August 1 2099" class="date neumos"></div>
      </div>
      <div class="buttons">
        <a class="tickets" href="https://www.axs.com/events/1/x-tickets?skin=neumos">Buy Tickets</a>
      </div>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.entry")
    assert _neumos_scraper()._parse_item(item, date.today(), None, "neumos") is None


def test_parse_item_returns_none_when_date_unparseable():
    html = """
    <div class="eventItem entry clearfix">
      <div class="info clearfix">
        <h3 class="title"><a href="https://www.neumos.com/events/detail/tbd-show-1">TBD Show</a></h3>
        <div aria-label="Someday Soon" class="date neumos"></div>
      </div>
      <div class="buttons">
        <a class="tickets" href="https://www.axs.com/events/1/tbd-show-tickets?skin=neumos">Buy Tickets</a>
      </div>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.entry")
    assert _neumos_scraper()._parse_item(item, date.today(), None, "neumos") is None


# --- scrape() end to end (fixture-driven, HTTP stubbed) ---


async def test_scrape_showbox_end_to_end_filters_to_configured_venue(monkeypatch):
    scraper = _showbox_scraper("The Showbox")

    async def _fake_fetch_soup(self, url, **kwargs):
        return _future_showbox_soup()

    monkeypatch.setattr(AEGVenueScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    names = {e.name for e in events}
    # "Alabama Shakes" (Dune Peninsula) and "Riot Ten" (TBD date) excluded.
    assert names == {"Earlybirds Club"}
    assert all(e.venue_slug == "showbox" for e in events)
    assert all(e.source == "aeg_venue" for e in events)


async def test_scrape_showbox_sodo_end_to_end(monkeypatch):
    scraper = _showbox_scraper("Showbox SoDo")

    async def _fake_fetch_soup(self, url, **kwargs):
        return _future_showbox_soup()

    monkeypatch.setattr(AEGVenueScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    names = {e.name for e in events}
    assert names == {"Dillstradamus (Dillon Francis B2B Flosstradamus)", "ISOxo presents: Hardcore Diva"}


async def test_scrape_neumos_end_to_end_returns_all_skin_neumos_cards(monkeypatch):
    scraper = _neumos_scraper("neumos")

    async def _fake_fetch_soup(self, url, **kwargs):
        return _future_neumos_soup()

    monkeypatch.setattr(AEGVenueScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    assert len(events) == 12
    assert all(e.venue_slug == "neumos" for e in events)
    assert all(e.source == "aeg_venue" for e in events)


def test_scrape_raises_without_url():
    scraper = AEGVenueScraper("showbox", {"venue_name": "The Showbox"})
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(scraper.scrape())


def test_scrape_raises_without_venue_name_or_skin():
    scraper = AEGVenueScraper("showbox", {"url": "https://www.showboxpresents.com/events/all"})
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(scraper.scrape())
