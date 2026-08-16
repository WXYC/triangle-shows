"""Tests for app.cadence — the shared cron/staleness knowledge behind both
configure_scheduler() and the scrape-health evaluator (issue #86, part 1).

group_for() must mirror ScrapeManager.scrape_indie's `Venue.scraper_type !=
"ticketmaster"` complement exactly (app/scrapers/manager.py) so a brand-new scraper
type classifies as "indie" without a lookup-table entry to remember.
"""

from dataclasses import dataclass

import pytest

from app.cadence import CRON_HOURS, cron_hour_string, group_for, max_gap_hours


@dataclass
class _FakeVenue:
    scraper_type: str


@pytest.mark.parametrize(
    "scraper_type,expected_group",
    [
        ("ticketmaster", "ticketmaster"),
        ("venuepilot", "indie"),
        ("mec", "indie"),
        ("tribe_events", "indie"),
        ("some_brand_new_scraper_type_not_yet_invented", "indie"),
    ],
)
def test_group_for_mirrors_the_managers_complement(scraper_type, expected_group):
    assert group_for(_FakeVenue(scraper_type=scraper_type)) == expected_group


def test_cron_hour_string_matches_the_historical_scheduler_literals():
    # These are the exact literals configure_scheduler() hardcoded before
    # extraction — pinned so the refactor can't silently change the schedule.
    assert cron_hour_string("ticketmaster") == "6,18"
    assert cron_hour_string("indie") == "6,12,18"


@pytest.mark.parametrize(
    "group,expected_hours",
    [
        # ticketmaster: 6 -> 18 is a 12h gap, 18 -> next day's 6 is also 12h.
        ("ticketmaster", 12.0),
        # indie: 6 -> 12 -> 18 are 6h gaps, but 18 -> next day's 6 is 12h --
        # the *max*, not the mean, is what must be reported.
        ("indie", 12.0),
    ],
)
def test_max_gap_hours_is_the_maximum_not_the_mean(group, expected_hours):
    assert max_gap_hours(group) == expected_hours


def test_cron_hours_table_is_the_single_source_scheduler_and_evaluator_share():
    assert CRON_HOURS["ticketmaster"] == (6, 18)
    assert CRON_HOURS["indie"] == (6, 12, 18)
