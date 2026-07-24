"""The Crocodile (Seattle) scraper: parse-layer behavior (issue #75).

Pure unit tests fed a real saved fixture (tests/fixtures/crocodile_calendar.html —
byte-for-byte excerpted from a live `curl` of calendar.thecrocodile.com; see that
file's header comment for exactly what it covers) — no database, no HTTP.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.scrapers.crocodile import CrocodileScraper

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "crocodile_calendar.html"


def _scraper() -> CrocodileScraper:
    return CrocodileScraper("crocodile", {"url": "https://calendar.thecrocodile.com/"})


def _soup() -> BeautifulSoup:
    return BeautifulSoup(_FIXTURE_PATH.read_text(), "lxml")


def _future_fixture_soup() -> BeautifulSoup:
    """The fixture's dates are anchored to 2026-07-24/09-29 (fetch day); rewrite
    the two "2026" years the test suite could otherwise outrun to a fixed offset
    from today, so `_parse_item`'s "skip past events" check can't flip these to
    past events as the wall clock advances past the original fetch date."""
    d1 = (date.today() + timedelta(days=30)).strftime("%b %d, %Y")
    d2 = (date.today() + timedelta(days=60)).strftime("%b %d, %Y")
    html = _FIXTURE_PATH.read_text()
    # Replace only the abbreviated-month (internal-link) date strings, which
    # drive `_parse_item`'s date/time parsing; the full-month (outbound-link)
    # strings are untouched — the two-link contract doesn't require them to agree.
    html = html.replace("Jul 24, 2026 10:00 PM", f"{d1} 10:00 PM")
    html = html.replace("Jul 25, 2026 6:00 PM", f"{d1} 6:00 PM")
    html = html.replace("Jul 25, 2026 10:00 PM", f"{d1} 10:00 PM")
    html = html.replace("Sep 29, 2026 8:00 PM", f"{d2} 8:00 PM")
    return BeautifulSoup(html, "lxml")


def test_fixture_yields_four_listings():
    soup = _soup()
    items = soup.select("div.uui-layout88_item")
    assert len(items) == 4


def test_parse_item_no_outbound_ticket_falls_back_to_own_detail_url():
    # "Dimelo - A Latin Experience": the outbound <a> is the Webflow placeholder
    # (href="#"), so both ticket_url and source_url must be the venue's own page.
    soup = _future_fixture_soup()
    item = next(
        i for i in soup.select("div.uui-layout88_item")
        if "Dimelo" in i.select_one("h3").get_text()
    )
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.source_url == "https://calendar.thecrocodile.com/shows/dimelo-a-latin-experience-25-jul"
    assert parsed.ticket_url == parsed.source_url


def test_parse_item_outbound_ticket_present_used_as_ticket_url_not_identity():
    # "FULTON LEE": outbound TicketWeb link is present — it must win as
    # ticket_url, but source_url (the identity field) must still be the venue's
    # own /shows/ page, never the outbound ticketer link (issue #75 audit).
    soup = _future_fixture_soup()
    item = next(
        i for i in soup.select("div.uui-layout88_item")
        if "FULTON LEE" in i.select_one("h3").get_text()
    )
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.ticket_url == (
        "https://www.ticketweb.com/event/fulton-lee-sing-with-me-the-crocodile-tickets/14108054?pl=crocodile"
    )
    assert parsed.source_url == (
        "https://calendar.thecrocodile.com/shows/fulton-lee-sing-with-me-tour-2026-25-jul"
    )


def test_parse_item_captures_time_from_internal_link_date_text():
    soup = _future_fixture_soup()
    item = next(
        i for i in soup.select("div.uui-layout88_item")
        if "FULTON LEE" in i.select_one("h3").get_text()
    )
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.show_time is not None
    assert (parsed.show_time.hour, parsed.show_time.minute) == (18, 0)


def test_parse_item_no_time_specified_leaves_show_time_none():
    # "Bella Kay: The Reckless Tour" carries a time (8:00 PM) in the real
    # fixture; assert the inverse contract directly against a hand-built card
    # with a date-only detail-link string, since every real fixture card here
    # happens to specify a time.
    html = """
    <div role="listitem" class="uui-layout88_item w-dyn-item">
      <a href="#" class="link-block-2 ex w-inline-block w-condition-invisible">
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">Cat Power</h3>
          <div class="text-block-71 cal-start-date">September 29, 2026</div>
        </div>
      </a>
      <a href="/shows/cat-power-29-sep" class="link-block-2 ex w-inline-block">
        <div class="uui-layout88_item-content">
          <h3 class="uui-heading-xxsmall-2">Cat Power</h3>
          <div class="text-block-71 cal-start-date">Sep 29, 2026</div>
        </div>
      </a>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.uui-layout88_item")
    future = (date.today() + timedelta(days=60)).strftime("%b %d, %Y")
    # `_parse_item` reads the date from the detail link's (second <a>)
    # `.cal-start-date`, so both occurrences must move into the future —
    # rewriting only the first (outbound-link) match would leave the detail
    # link's hardcoded "Sep 29, 2026" to eventually go stale and start
    # tripping the "skip past events" branch.
    for el in item.select(".cal-start-date"):
        el.string = future
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.show_time is None


def test_parse_item_visible_sold_out_tag_sets_status():
    # "Bella Kay: The Reckless Tour" is the fixture's one live (non-Webflow-
    # conditional) sold-out tag.
    soup = _future_fixture_soup()
    item = next(
        i for i in soup.select("div.uui-layout88_item")
        if "Bella Kay" in i.select_one("h3").get_text()
    )
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.status == "sold_out"


def test_parse_item_conditionally_invisible_sold_out_tag_leaves_default_status():
    soup = _future_fixture_soup()
    item = next(
        i for i in soup.select("div.uui-layout88_item")
        if "Dimelo" in i.select_one("h3").get_text()
    )
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.status == "on_sale"


def test_parse_item_reschedule_evidence_slug_disagrees_with_date_but_still_used():
    # Live evidence for the URL_IDENTITY=TRUSTED audit: "SOS: The Recession Pop
    # Party"'s own slug carries "18-jul" while its rendered date is the 25th —
    # the slug doesn't track the date field, so it survives a reschedule. The
    # scraper must still use it as source_url regardless of the mismatch.
    soup = _future_fixture_soup()
    item = next(
        i for i in soup.select("div.uui-layout88_item")
        if "SOS" in i.select_one("h3").get_text()
    )
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.source_url == (
        "https://calendar.thecrocodile.com/shows/sos-the-recession-pop-party-18-jul"
    )


def test_parse_item_returns_none_when_missing_a_link():
    html = """
    <div role="listitem" class="uui-layout88_item w-dyn-item">
      <a href="/shows/only-one-link" class="link-block-2 ex w-inline-block">
        <h3 class="uui-heading-xxsmall-2">Only One Link</h3>
        <div class="text-block-71 cal-start-date">Sep 29, 2026</div>
      </a>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.uui-layout88_item")
    assert _scraper()._parse_item(item, date.today()) is None


def test_parse_item_returns_none_when_date_unparseable():
    html = """
    <div role="listitem" class="uui-layout88_item w-dyn-item">
      <a href="#" class="link-block-2 ex w-inline-block w-condition-invisible">
        <h3 class="uui-heading-xxsmall-2">Mystery Date</h3>
        <div class="text-block-71 cal-start-date">TBD</div>
      </a>
      <a href="/shows/mystery-date" class="link-block-2 ex w-inline-block">
        <h3 class="uui-heading-xxsmall-2">Mystery Date</h3>
        <div class="text-block-71 cal-start-date">TBD</div>
      </a>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.uui-layout88_item")
    assert _scraper()._parse_item(item, date.today()) is None


def test_parse_item_skips_past_events():
    html = """
    <div role="listitem" class="uui-layout88_item w-dyn-item">
      <a href="#" class="link-block-2 ex w-inline-block w-condition-invisible">
        <h3 class="uui-heading-xxsmall-2">Old Show</h3>
        <div class="text-block-71 cal-start-date">Jan 01, 2020</div>
      </a>
      <a href="/shows/old-show" class="link-block-2 ex w-inline-block">
        <h3 class="uui-heading-xxsmall-2">Old Show</h3>
        <div class="text-block-71 cal-start-date">Jan 01, 2020</div>
      </a>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.uui-layout88_item")
    assert _scraper()._parse_item(item, date.today()) is None


@pytest.mark.parametrize("outbound_href", ["#", ""])
def test_parse_item_placeholder_or_blank_outbound_href_falls_back(outbound_href):
    html = f"""
    <div role="listitem" class="uui-layout88_item w-dyn-item">
      <a href="{outbound_href}" class="link-block-2 ex w-inline-block w-condition-invisible">
        <h3 class="uui-heading-xxsmall-2">No Outbound Yet</h3>
        <div class="text-block-71 cal-start-date">Sep 29, 2026</div>
      </a>
      <a href="/shows/no-outbound-yet" class="link-block-2 ex w-inline-block">
        <h3 class="uui-heading-xxsmall-2">No Outbound Yet</h3>
        <div class="text-block-71 cal-start-date">Sep 29, 2026</div>
      </a>
    </div>
    """
    item = BeautifulSoup(html, "lxml").select_one("div.uui-layout88_item")
    future = (date.today() + timedelta(days=60)).strftime("%b %d, %Y")
    for el in item.select(".cal-start-date"):
        el.string = future
    parsed = _scraper()._parse_item(item, date.today())
    assert parsed is not None
    assert parsed.ticket_url == parsed.source_url


async def test_scrape_end_to_end_on_fixture_returns_all_upcoming_listings(monkeypatch):
    """Feeds the whole saved fixture through scrape() via a stubbed fetch_soup —
    the real-fixture equivalent of "scrapes end to end" without live HTTP."""
    scraper = _scraper()

    async def _fake_fetch_soup(self, url, **kwargs):
        return _future_fixture_soup()

    monkeypatch.setattr(CrocodileScraper, "fetch_soup", _fake_fetch_soup)

    events = await scraper.scrape()
    names = {e.name for e in events}
    assert names == {
        "Dimelo - A Latin Experience",
        "FULTON LEE: Sing With Me Tour 2026",
        "SOS: The Recession Pop Party",
        "Bella Kay: The Reckless Tour",
    }
    assert all(e.venue_slug == "crocodile" for e in events)
    assert all(e.source == "crocodile" for e in events)
