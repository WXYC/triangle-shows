"""Carolina Theatre (Durham) scraper: parse-layer behavior.

Pure unit tests — the parse method is fed a constructed HTML fragment taken from
the live events page, so there is no database and no HTTP. The scraper fetches via
the shared BaseScraper.fetch_soup path (current-Chrome browser headers); the
stale-User-Agent regression guard lives with that shared path in
tests/test_fetch_soup.py, so it isn't duplicated here.
"""

from datetime import date, timedelta

import pytest
from bs4 import BeautifulSoup

from app.scrapers.carolina_theatre import CarolinaTheatreScraper

# A single event card modeled on the live markup at https://carolinatheatre.org/events/
# — real CSS classes/structure the scraper's selectors (div.eventCard, p.card__title,
# .event__dateBox .day/.month, div.card__info p > i.fa-clock, .eventCard__image img)
# target, with a WXYC-representative act (Jessica Pratt) swapped in for the title.
# Kept realistic so a future markup drift breaks this test.
_CARD_HTML = """
<div class="card eventCard event" data-filterme=" event ">
  <a href="https://carolinatheatre.org/jessica-pratt-26/">
    <div class="event__dateBox">
      <span class="day">9</span>
      <span class="month">Aug</span>
    </div>
    <div class="eventCard__image">
      <img alt="" src="https://carolinatheatre.org/wp-content/uploads/JessicaPratt-500x280.jpg"/>
    </div>
    <div class="card__infoWrapper">
      <p class="event__categories">Concert</p>
      <p class="card__title">Jessica Pratt</p>
      <div class="card__info">
        <p><i class="far fa-clock"></i> 8:00pm</p>
        <p><i class="far fa-map-marker-alt"></i> Fletcher Hall</p>
      </div>
    </div>
  </a>
</div>
"""


def _scraper() -> CarolinaTheatreScraper:
    return CarolinaTheatreScraper(
        "carolina-theatre", {"url": "https://carolinatheatre.org/events/"}
    )


def _card():
    return BeautifulSoup(_CARD_HTML, "lxml").select_one("div.eventCard")


def test_parse_card_extracts_all_fields():
    parsed = _scraper()._parse_card(_card())
    assert parsed is not None
    assert parsed.name == "Jessica Pratt"
    assert parsed.artist == "Jessica Pratt"
    assert parsed.date.month == 8 and parsed.date.day == 9
    assert parsed.show_time is not None
    assert (parsed.show_time.hour, parsed.show_time.minute) == (20, 0)
    assert parsed.image_url == (
        "https://carolinatheatre.org/wp-content/uploads/JessicaPratt-500x280.jpg"
    )


def test_parse_card_source_url_is_per_event_detail_page():
    # The TRUSTED verdict depends on source_url being the event's own detail URL
    # (never the shared listing page), so identity can key on url:.
    parsed = _scraper()._parse_card(_card())
    assert parsed.source_url == "https://carolinatheatre.org/jessica-pratt-26/"
    assert parsed.ticket_url == "https://carolinatheatre.org/jessica-pratt-26/"


def test_parse_card_returns_none_when_title_missing():
    html = _CARD_HTML.replace('<p class="card__title">Jessica Pratt</p>', "")
    card = BeautifulSoup(html, "lxml").select_one("div.eventCard")
    assert _scraper()._parse_card(card) is None


def test_parse_card_returns_none_when_date_missing():
    html = _CARD_HTML.replace('<span class="day">9</span>', "")
    card = BeautifulSoup(html, "lxml").select_one("div.eventCard")
    assert _scraper()._parse_card(card) is None


def test_parse_day_month_near_future_stays_current_year():
    # A month/day a few weeks out keeps the current year (the roll-forward only
    # fires for dates well in the past). Anchored on today so it can't drift.
    today = date.today()
    future = today + timedelta(days=30)
    if future.year != today.year:
        pytest.skip("year boundary — the well-past roll-forward test covers the intent")
    parsed = _scraper()._parse_day_month(str(future.day), future.strftime("%b"))
    assert parsed == date(today.year, future.month, future.day)


def test_parse_day_month_well_past_rolls_to_next_year():
    # A month/day well in the past (more than a week ago) rolls to next year, so
    # a listing card that only shows day+month never back-dates an upcoming show.
    today = date.today()
    past = today - timedelta(days=90)
    parsed = _scraper()._parse_day_month(str(past.day), past.strftime("%b"))
    # The intended result is the next future occurrence of that day/month.
    if past.year == today.year:
        expected = date(today.year + 1, past.month, past.day)
    else:
        # `past` already wrapped into last year; its day/month this year is future.
        expected = date(today.year, past.month, past.day)
    assert parsed == expected
    assert parsed > today
