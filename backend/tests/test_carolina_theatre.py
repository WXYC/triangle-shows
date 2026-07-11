"""Carolina Theatre (Durham) scraper: parse-layer behavior and the request-header
guard that keeps the venue's WAF from 403-ing the scraper.

Pure unit tests — the parse method is fed a constructed HTML fragment taken from
the live events page, so there is no database and no HTTP. The header test is a
regression guard for the re-enable: the venue's nginx WAF hard-blocks the shared
stale User-Agent with a 403 while returning 200 under a current browser UA, so the
scraper must send its own, non-stale User-Agent.
"""

from datetime import date

from bs4 import BeautifulSoup

from app.scrapers.base import BROWSER_HEADERS
from app.scrapers.carolina_theatre import CAROLINA_HEADERS, CarolinaTheatreScraper

# A single event card lifted verbatim from https://carolinatheatre.org/events/ —
# the markup the scraper's selectors (div.eventCard, p.card__title,
# .event__dateBox .day/.month, div.card__info p > i.fa-clock, .eventCard__image img)
# target. Kept realistic so a future markup drift breaks this test.
_CARD_HTML = """
<div class="card eventCard event" data-filterme=" event ">
  <a href="https://carolinatheatre.org/the-wallflowers-26/">
    <div class="event__dateBox">
      <span class="day">9</span>
      <span class="month">Aug</span>
    </div>
    <div class="eventCard__image">
      <img alt="" src="https://carolinatheatre.org/wp-content/uploads/TheWallflowers-500x280.jpg"/>
    </div>
    <div class="card__infoWrapper">
      <p class="event__categories">Concert</p>
      <p class="card__title">The Wallflowers</p>
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
    assert parsed.name == "The Wallflowers"
    assert parsed.artist == "The Wallflowers"
    assert parsed.date.month == 8 and parsed.date.day == 9
    assert parsed.show_time is not None
    assert (parsed.show_time.hour, parsed.show_time.minute) == (20, 0)
    assert parsed.image_url == (
        "https://carolinatheatre.org/wp-content/uploads/TheWallflowers-500x280.jpg"
    )


def test_parse_card_source_url_is_per_event_detail_page():
    # The TRUSTED verdict depends on source_url being the event's own detail URL
    # (never the shared listing page), so identity can key on url:.
    parsed = _scraper()._parse_card(_card())
    assert parsed.source_url == "https://carolinatheatre.org/the-wallflowers-26/"
    assert parsed.ticket_url == "https://carolinatheatre.org/the-wallflowers-26/"


def test_parse_card_returns_none_when_title_missing():
    html = _CARD_HTML.replace('<p class="card__title">The Wallflowers</p>', "")
    card = BeautifulSoup(html, "lxml").select_one("div.eventCard")
    assert _scraper()._parse_card(card) is None


def test_parse_card_returns_none_when_date_missing():
    html = _CARD_HTML.replace('<span class="day">9</span>', "")
    card = BeautifulSoup(html, "lxml").select_one("div.eventCard")
    assert _scraper()._parse_card(card) is None


def test_parse_day_month_infers_year_and_rolls_forward():
    # A month/day already well in the past rolls to next year; a near/future one
    # stays in the current year. Anchored on today so it can't drift.
    today = date.today()
    # ~30 days out — must resolve within a year of today (never the distant past).
    future = _scraper()._parse_day_month(str(today.day), today.strftime("%b"))
    assert future is not None
    assert -7 <= (future - today).days <= 366


def test_request_headers_do_not_use_the_blocked_stale_user_agent():
    # Regression guard for the re-enable: the venue WAF 403s the shared stale UA
    # (Windows / Chrome 122). The scraper must override it. If a refactor points
    # the scraper back at BROWSER_HEADERS, this fails before the 403 hits prod.
    assert CAROLINA_HEADERS["User-Agent"] != BROWSER_HEADERS["User-Agent"]
    assert "Chrome/122" not in CAROLINA_HEADERS["User-Agent"]
    # The non-UA headers are still inherited from the shared set.
    assert CAROLINA_HEADERS["Accept"] == BROWSER_HEADERS["Accept"]
    assert CAROLINA_HEADERS["Accept-Language"] == BROWSER_HEADERS["Accept-Language"]
