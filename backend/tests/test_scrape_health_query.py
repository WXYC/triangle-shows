"""Tests for the shared ScrapeLog fetch (app.services.scrape_health_query).

This module is the one thing GET /api/v1/health/scrapers and the daily digest job
both read their rows through, so the row cap and the baseline window are a contract
between two surfaces rather than an implementation detail of either. Both surfaces
have their own tests that reach this code indirectly; these pin it directly, because
the failure it exists to prevent is silent and inverted -- a truncated history reads
as "this venue never shows events, so its zeros are normal" and reports ok for a
venue sitting behind a fresh bot wall.
"""

from datetime import datetime, timedelta

from app.models import ScrapeLog
from app.services.scrape_health_query import (
    RECENT_SCRAPE_LOG_LIMIT,
    fetch_venue_scrape_logs,
)


def _add_log(session, venue, *, started_at, status="success", events_found=0):
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type=venue.scraper_type,
            status=status,
            events_found=events_found,
            started_at=started_at,
            finished_at=started_at + timedelta(seconds=5),
        )
    )


async def test_the_baseline_row_is_returned_even_when_the_cap_cannot_reach_it(session, make_venue):
    """The capped slice is bounded by row count, not by time, so a high-frequency
    venue's cap can span less wall-clock time than the 30-day baseline window. The
    separately-fetched baseline row is what keeps the evaluator's silent-zero guard
    honest in that case -- without it the guard sees no normal activity and inverts
    a genuine warning into ok."""
    venue = await make_venue(slug="capped-out")
    now = datetime.utcnow()

    # More zero-event successes than the cap, at a cadence dense enough that the cap
    # reaches back only ~15 days -- half the baseline window.
    for i in range(RECENT_SCRAPE_LOG_LIMIT + 20):
        _add_log(session, venue, started_at=now - timedelta(hours=2 * i))
    # The venue's last healthy scrape: inside the 30-day window, well outside the cap.
    baseline_at = now - timedelta(days=28)
    _add_log(session, venue, started_at=baseline_at, events_found=9)
    await session.commit()

    logs = await fetch_venue_scrape_logs(session, venue.id, now=now)

    assert len(logs) == RECENT_SCRAPE_LOG_LIMIT + 1, (
        "expected the capped slice plus exactly one appended baseline row"
    )
    assert any(
        log.events_found == 9 and log.started_at == baseline_at for log in logs
    ), "the baseline row the cap could not reach was not fetched separately"


async def test_a_baseline_row_already_inside_the_cap_is_not_duplicated(session, make_venue):
    """Both queries run in one session, so SQLAlchemy's identity map hands back the
    same object for a row the capped fetch already had. Appending it again would put
    a duplicate at the end of the list -- harmless-looking, but the evaluator slices
    its consecutive window off the sorted rows, so a duplicate can corrupt it."""
    venue = await make_venue(slug="baseline-in-range")
    now = datetime.utcnow()

    _add_log(session, venue, started_at=now - timedelta(hours=30), events_found=7)
    for hours_ago in (18, 12, 6):
        _add_log(session, venue, started_at=now - timedelta(hours=hours_ago))
    await session.commit()

    logs = await fetch_venue_scrape_logs(session, venue.id, now=now)

    assert len(logs) == 4
    assert len({id(log) for log in logs}) == 4
    assert len({log.id for log in logs}) == 4


async def test_rows_newer_than_now_are_excluded_from_both_queries(session, make_venue):
    """`now` bounds the capped slice and the baseline query alike. That is what makes
    the digest's replay at an earlier instant a true replay: a caller asking "as of
    yesterday" must not be handed rows that hadn't happened yet, in either query."""
    venue = await make_venue(slug="replayed")
    now = datetime.utcnow()
    as_of = now - timedelta(hours=24)

    # A nonzero success inside the last 24h -- the newest baseline candidate overall,
    # but invisible to a caller evaluating as of 24h ago.
    _add_log(session, venue, started_at=now - timedelta(hours=3), events_found=11)
    _add_log(session, venue, started_at=now - timedelta(hours=36), events_found=4)
    await session.commit()

    logs = await fetch_venue_scrape_logs(session, venue.id, now=as_of)

    assert [log.events_found for log in logs] == [4]


async def test_a_venue_with_no_rows_returns_an_empty_list(session, make_venue):
    """A never-scraped venue is the `unknown` verdict's input, and it has to arrive as
    an empty list rather than an error -- both surfaces evaluate every venue in the
    table, including one added to venues.toml minutes ago."""
    venue = await make_venue(slug="never-scraped")
    await session.commit()

    assert await fetch_venue_scrape_logs(session, venue.id, now=datetime.utcnow()) == []
