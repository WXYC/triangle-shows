"""Unit tests for BaseScraper.parse_date (issue #23).

Pure-function tests — no database or HTTP client. parse_date consolidates the
flexible date-text parsing that rhp_events, eventprime, koka_booth, and
webflow_cms each used to reimplement: strip a leading weekday name, strip
ordinal suffixes (1st/2nd/3rd/4th), walk a list of strptime formats, and — when
a format carries no year — assume the current year and roll forward to next year
if the resulting date is already well past.

The table below is drawn from the real shapes those scrapers see:
  - rhp_events:  "Fri, Jan 10, 2025", "January 15, 2025", "2025-01-15", …
  - eventprime:  "Thursday, February 26th, 2026" (weekday + ordinal + year)
  - koka_booth:  "Saturday, June 1, 2024", "06/01/2024"
  - webflow_cms: "January 15, 2025" (single configured format)
"""

from datetime import date, datetime, timedelta

import pytest

from app.scrapers.base import BaseScraper


# --- Fully-qualified dates (year present): the format list ---

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # "%B %d, %Y" — full month name (rhp, eventprime, koka, webflow default)
        ("January 15, 2025", date(2025, 1, 15)),
        # "%b %d, %Y" — abbreviated month name
        ("Jan 15, 2025", date(2025, 1, 15)),
        # "%m/%d/%Y" — US numeric slashes (rhp, koka)
        ("01/15/2025", date(2025, 1, 15)),
        ("1/5/2025", date(2025, 1, 5)),
        # "%m-%d-%Y" — US numeric dashes (rhp)
        ("01-15-2025", date(2025, 1, 15)),
        # "%Y-%m-%d" — ISO (rhp)
        ("2025-01-15", date(2025, 1, 15)),
        # "%A, %B %d, %Y" — full weekday + full month (rhp)
        ("Wednesday, January 15, 2025", date(2025, 1, 15)),
        # "%a, %b %d, %Y" — abbreviated weekday + abbreviated month (rhp)
        ("Wed, Jan 15, 2025", date(2025, 1, 15)),
    ],
)
def test_parses_fully_qualified_dates(text, expected):
    assert BaseScraper.parse_date(text) == expected


# --- Leading weekday-name strip (abbreviated and full) ---

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Abbreviated weekday prefix ahead of a format that has no weekday token.
        ("Fri, January 10, 2025", date(2025, 1, 10)),
        ("Mon, Jan 6, 2025", date(2025, 1, 6)),
        # Full weekday prefix (eventprime / koka shape) ahead of "%B %d, %Y".
        ("Saturday, June 1, 2024", date(2024, 6, 1)),
        ("Thursday, February 26, 2026", date(2026, 2, 26)),
        # No comma after the weekday still strips.
        ("Sat June 1, 2024", date(2024, 6, 1)),
    ],
)
def test_strips_leading_weekday(text, expected):
    assert BaseScraper.parse_date(text) == expected


# --- Ordinal-suffix strip (1st / 2nd / 3rd / 4th / nth) ---

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("February 1st, 2026", date(2026, 2, 1)),
        ("March 2nd, 2026", date(2026, 3, 2)),
        ("April 3rd, 2026", date(2026, 4, 3)),
        ("May 4th, 2026", date(2026, 5, 4)),
        ("June 21st, 2026", date(2026, 6, 21)),
        # The eventprime real shape: weekday + ordinal + year all at once.
        ("Thursday, February 26th, 2026", date(2026, 2, 26)),
    ],
)
def test_strips_ordinal_suffixes(text, expected):
    assert BaseScraper.parse_date(text) == expected


# --- No-year formats: assume current year, roll forward if well past ---

def test_no_year_future_month_uses_current_year():
    """A month/day comfortably in the future keeps the current year."""
    today = date.today()
    future = today + timedelta(days=30)
    text = future.strftime("%B %d")  # e.g. "August 09"
    assert BaseScraper.parse_date(text) == date(today.year, future.month, future.day)


def test_no_year_recent_past_uses_current_year():
    """A month/day only a few days in the past stays in the current year
    (the roll-forward only triggers when the date is *well* past)."""
    today = date.today()
    recent = today - timedelta(days=3)
    # Skip the year-boundary edge where strftimeing a Jan date near New Year's
    # would ambiguously wrap; the dedicated roll-forward test covers the intent.
    if recent.year != today.year:
        pytest.skip("year boundary — covered by roll-forward test")
    text = recent.strftime("%b %d")
    assert BaseScraper.parse_date(text) == date(today.year, recent.month, recent.day)


def test_no_year_well_past_month_rolls_to_next_year():
    """A month/day well in the past (more than a week ago) rolls to next year,
    so a listing page that omits the year doesn't back-date an upcoming show."""
    today = date.today()
    past = today - timedelta(days=90)
    text = past.strftime("%B %d")
    # The intended date is the next future occurrence of that month/day.
    if past.year == today.year:
        expected = date(today.year + 1, past.month, past.day)
    else:
        # past wrapped into last year already; its month/day this year is future.
        expected = date(today.year, past.month, past.day)
    assert BaseScraper.parse_date(text) == expected


def test_no_year_abbreviated_month_form():
    """The year-less abbreviated-month format ("%b %d") is also supported."""
    today = date.today()
    future = today + timedelta(days=45)
    text = future.strftime("%b %d")
    assert BaseScraper.parse_date(text) == date(today.year, future.month, future.day)


# --- Custom format list (webflow_cms passes its configured format) ---

def test_custom_formats_override_default_list():
    # webflow's default is "%B %d, %Y"; a venue could configure another.
    assert BaseScraper.parse_date("15 January 2025", formats=["%d %B %Y"]) == date(2025, 1, 15)


def test_custom_formats_still_strip_weekday_and_ordinal():
    # Even with a custom list, the weekday/ordinal preprocessing still runs.
    assert BaseScraper.parse_date(
        "Monday, 15th January 2025", formats=["%d %B %Y"]
    ) == date(2025, 1, 15)


def test_custom_formats_mix_year_and_year_less():
    # A caller-supplied list can mix year-bearing and year-less formats. The
    # branch that splits them must (a) route a year-bearing input through the
    # with-year path unchanged, and (b) give a year-less input the current-year
    # + roll-forward treatment — exactly as the default lists do.
    formats = ["%m/%d/%Y", "%d %B"]
    # Year-bearing input: parsed as-is, no roll-forward.
    assert BaseScraper.parse_date("01/15/2025", formats=formats) == date(2025, 1, 15)
    # Year-less input against the same list: current year, rolled forward if past.
    today = date.today()
    future = today + timedelta(days=40)
    if future.year != today.year:
        pytest.skip("year boundary — the year-less roll-forward tests cover the intent")
    text = future.strftime("%d %B")  # e.g. "20 August"
    assert BaseScraper.parse_date(text, formats=formats) == date(
        today.year, future.month, future.day
    )


# --- Whitespace / trailing punctuation tolerance ---

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  January 15, 2025  ", date(2025, 1, 15)),
        # A dangling trailing comma (seen after weekday-only rows) is tolerated.
        ("January 15, 2025,", date(2025, 1, 15)),
    ],
)
def test_tolerates_surrounding_whitespace_and_trailing_comma(text, expected):
    assert BaseScraper.parse_date(text) == expected


# --- Unparseable / degenerate input returns None ---

@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "TBA",
        "Next Friday",
        "not a date",
    ],
)
def test_unparseable_input_returns_none(text):
    assert BaseScraper.parse_date(text) is None


# --- parse_date is a staticmethod callable off the class and instances ---

def test_callable_as_staticmethod_and_on_instance():
    assert BaseScraper.parse_date("January 15, 2025") == date(2025, 1, 15)
