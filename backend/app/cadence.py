"""
Scrape cadence — the shared cron/staleness knowledge behind both the scheduler and
the scrape-health evaluator.

Role: ``group_for()`` mirrors ``ScrapeManager.scrape_indie``'s
``Venue.scraper_type != "ticketmaster"`` complement (``app/scrapers/manager.py``) —
the "indie" group is not an enumerable set of scraper_type values, so a lookup table
keyed by scraper type would leave every future scraper type unclassified. This is a
leaf module, sibling of ``app.market_time`` (which exists for exactly this reason —
"the scrape manager can share it without depending on the API layer"): ``app/scheduler.py``
imports ``app.database``, ``app.models``, and ``app.scrapers.manager``, so having
``app/services/scrape_health.py`` or ``app/api/v1.py`` import ``group_for`` *from*
``scheduler.py`` would drag the whole scraper stack into the request path.
``scheduler.py``, ``services/scrape_health.py``, and ``api/v1.py`` all import from here
instead (issue #86, correction comment 2026-08-16).

Requires: nothing outside the standard library. ``group_for`` takes anything with a
``scraper_type`` attribute (see ``_HasScraperType`` below) rather than importing
``app.models.Venue``, keeping this module import-light on purpose.
"""

from typing import Literal, Protocol

# --- Types ---

ScrapeGroup = Literal["ticketmaster", "indie"]


class _HasScraperType(Protocol):
    scraper_type: str


# --- Cadence table (single source of truth) ---

# Cron hours (wall-clock, region market timezone) per group. configure_scheduler()
# builds its CronTrigger(hour=...) argument from these via cron_hour_string() below;
# keep this table and the scheduled jobs in sync by construction rather than by hand.
CRON_HOURS: dict[ScrapeGroup, tuple[int, ...]] = {
    "ticketmaster": (6, 18),
    "indie": (6, 12, 18),
}


def group_for(venue: _HasScraperType) -> ScrapeGroup:
    """Classify a venue into its scrape group.

    Mirrors ``ScrapeManager.scrape_indie``'s ``Venue.scraper_type != "ticketmaster"``
    complement exactly (``app/scrapers/manager.py``), so a brand-new scraper type
    classifies as "indie" correctly by construction, with no lookup-table entry to
    remember to add.
    """
    return "ticketmaster" if venue.scraper_type == "ticketmaster" else "indie"


def cron_hour_string(group: ScrapeGroup) -> str:
    """The cron 'hour' field value for `group`, e.g. "6,18" — the exact shape
    ``CronTrigger(hour=...)`` expects, matching ``configure_scheduler()``'s former
    hardcoded literals byte-for-byte.
    """
    return ",".join(str(hour) for hour in CRON_HOURS[group])


def _max_gap_hours(hours: tuple[int, ...]) -> float:
    """The maximum inter-run gap implied by a group's cron hours, wrapping past
    midnight. This is expected cadence for staleness purposes — the *maximum* gap,
    not the mean: a 2x-mean threshold would false-alarm every night for a group
    whose run hours aren't evenly spread (e.g. the indie group's 6/12/18 has a 6h
    day gap but a 12h overnight one). Wall-clock cron hours already absorb the DST
    fall-back day, since CronTrigger(timezone=tz) fires wall-clock, not elapsed time.
    """
    ordered = sorted(hours)
    gaps = [later - earlier for earlier, later in zip(ordered, ordered[1:])]
    gaps.append(ordered[0] + 24 - ordered[-1])  # wrap-around (last run -> next day's first)
    return float(max(gaps))


MAX_GAP_HOURS: dict[ScrapeGroup, float] = {
    group: _max_gap_hours(hours) for group, hours in CRON_HOURS.items()
}


def max_gap_hours(group: ScrapeGroup) -> float:
    """Expected maximum gap between consecutive runs of `group`, in hours."""
    return MAX_GAP_HOURS[group]
