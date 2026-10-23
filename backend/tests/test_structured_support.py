"""Support acts are one atomic name per element at the source (issue #130).

VenuePilot and RHP hand over support acts as one free-text blob ("w/ Gray Young,
Delver"); ``normalize_structured_support`` turns that blob into names at those two call
sites only. The billing-tail grammar also splits a spaced " / " list, but only inside an
already-isolated tail, so a " / " headliner co-bill keeps its headliner.
"""

import pytest

from app.scrapers.headliner import normalize_structured_support, parse_billing

# --- normalize_structured_support ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Observed VenuePilot strings (Stanczyks, Rubies).
        ("w/ Daniel Ouimet", ["Daniel Ouimet"]),
        ("w/ Gray Young, Delver", ["Gray Young", "Delver"]),
        (
            "w/ Survival Tactics, Guard Dog, Colo, asleepbeneaththesoil, UNIITY, Dear Lucy & Friends",
            ["Survival Tactics", "Guard Dog", "Colo", "asleepbeneaththesoil", "UNIITY", "Dear Lucy & Friends"],
        ),
        ("with Lamplight", ["Lamplight"]),
        ("with Bee Million", ["Bee Million"]),
        ("W/ Stereolab", ["Stereolab"]),
        # Observed RHP strings: a comma list, with "and" inside a name.
        ("Tobacco Road, Wim Tapley and The Cannons", ["Tobacco Road", "Wim Tapley and The Cannons"]),
        # Oxford "and"/"&" belongs to the list, not the last act.
        ("w/ Musashi Xero, End Of Your Rope, and Hazrdpay", ["Musashi Xero", "End Of Your Rope", "Hazrdpay"]),
        ("Stereolab, Cat Power, & Juana Molina", ["Stereolab", "Cat Power", "Juana Molina"]),
        # A spaced slash separates acts.
        ("Princess Superstar / Tony & The Kiki / Mz Neon", ["Princess Superstar", "Tony & The Kiki", "Mz Neon"]),
        # Lead-ins and placeholders name no act.
        ("with special guests Hex Files", ["Hex Files"]),
        ("Special Guests: Hex Files, Mean Habit", ["Hex Files", "Mean Habit"]),
        ("Hex Files, and more", ["Hex Files"]),
        ("special guests TBA", []),
        ("Special Guest", []),
        # Guards: these stay whole.
        ("AC/DC", ["AC/DC"]),
        ("Angel w/o Wings", ["Angel w/o Wings"]),
        ("w/o Wings", ["w/o Wings"]),
        ("With Honor", ["With Honor"]),
        ("With Confidence", ["With Confidence"]),
        ("Wim Tapley and The Cannons", ["Wim Tapley and The Cannons"]),
        ("", []),
        ("   ", []),
    ],
)
def test_normalize_structured_support(raw, expected):
    assert normalize_structured_support(raw) == expected


# --- parse_billing: spaced slash inside the tail only ---


@pytest.mark.parametrize(
    ("billing", "headliner", "support"),
    [
        (
            "Lords of Acid: Cheeky Freaky Tour w/ Princess Superstar / Tony & The Kiki / Mz Neon",
            "Lords of Acid: Cheeky Freaky Tour",
            ["Princess Superstar", "Tony & The Kiki", "Mz Neon"],
        ),
        # A " / " co-bill with no tail marker keeps its headliner unchanged.
        ("Wednesday 13 / Dead on a Sunday / Above Snakes", "Wednesday 13 / Dead on a Sunday / Above Snakes", []),
        # Oxford "and" in a tail; a capitalized "And" is part of a name.
        ("Wednesday w/ Truth Club, DJ Nu-Mark, and Sluice", "Wednesday", ["Truth Club", "DJ Nu-Mark", "Sluice"]),
        ("Deerhoof w/ Sword II, And Also The Trees", "Deerhoof", ["Sword II", "And Also The Trees"]),
        ("AC/DC w/ Sword II", "AC/DC", ["Sword II"]),
    ],
)
def test_parse_billing_splits_spaced_slash_in_tail(billing, headliner, support):
    assert parse_billing(billing) == (headliner, support)


# --- VenuePilot call site ---


@pytest.mark.parametrize(
    ("support", "expected"),
    [
        ("w/ Gray Young, Delver", ["Gray Young", "Delver"]),
        ("with Lamplight", ["Lamplight"]),
        (None, []),
    ],
)
def test_venuepilot_support_is_normalized(support, expected):
    from app.scrapers.venuepilot import VenuePilotScraper

    item = {"id": 1, "name": "Chuquimamani-Condori", "date": "2026-12-11", "support": support}
    parsed = VenuePilotScraper("test-venue", {"account_id": "1"})._parse_event(item)
    assert parsed is not None
    assert parsed.support_artists == expected


# --- RHP call site ---


def _rhp_card(inner: str):
    from bs4 import BeautifulSoup

    html = f"""
    <div class="eventWrapper rhpSingleEvent">
      <a class="url" href="https://catscradle.com/event/harvey-street/">
        <h2 class="rhp-event__title--list">Harvey Street</h2>
      </a>
      <time datetime="2026-12-11">Thu, Dec 11</time>
      {inner}
    </div>"""
    return BeautifulSoup(html, "html.parser").select_one(".eventWrapper")


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        # The listing card's subheader is the clean support line.
        (
            '<h4 class="eventSubHeader">Tobacco Road, Wim Tapley and The Cannons</h4>',
            ["Tobacco Road", "Wim Tapley and The Cannons"],
        ),
        # A <br> separates acts the way a comma does.
        ('<h4 class="eventSubHeader">Hex Files<br>Mean Habit</h4>', ["Hex Files", "Mean Habit"]),
        # An existing support selector wins over the subheader, whatever the document order.
        (
            '<h4 class="eventSubHeader">Tour Name Here</h4><div class="eventSupport">Stereolab, Cat Power</div>',
            ["Stereolab", "Cat Power"],
        ),
        ("", []),
    ],
)
def test_rhp_support_reads_subheader_as_fallback(inner, expected):
    from app.scrapers.rhp_events import RHPEventsScraper

    scraper = RHPEventsScraper("cats-cradle", {"url": "https://catscradle.com/events/"})
    parsed = scraper._parse_event(_rhp_card(inner), None)
    assert parsed is not None
    assert parsed.support_artists == expected
