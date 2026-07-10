"""Behavior tests for the vanished-event signal (issue #9).

Each successful venue scrape is a full snapshot of that venue's listing window.
Events that go missing from the snapshot accrue at most one miss per Triangle
calendar day; two misses on distinct days stamp a soft tombstone (removed_at).
These tests drive the whole path through the public entry point
``ScrapeManager.scrape_venue`` with a stubbed scraper — never the diff internals —
so the guards (failed/zero-event scrapes, horizon, per-day cap, reset on
reappearance) are exercised exactly as production scrapes would hit them.
"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Event, EventMissState
from app.scheduler import cleanup_past_events_job
from app.scrapers.base import ScrapedEvent
from app.scrapers.manager import ScrapeManager
from conftest import DEFAULT_EVENT_DATE as D  # a month in the future

# Scrape days are anchored to the real clock: candidacy requires the event date
# to be >= "today in the Triangle", and D is a month out, so all of these stay
# in-window no matter when the suite runs.
DAY1 = date.today()
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)


def _listing(venue_slug: str, artist: str, on_date: date = D) -> ScrapedEvent:
    """One entry of a venue's scraped listing page."""
    return ScrapedEvent(name=artist, artist=artist, date=on_date, venue_slug=venue_slug, source="stub")


class _StubScraper:
    """Stands in for a venue scraper: returns a fixed listing or raises."""

    def __init__(self, events, error):
        self._events = events
        self._error = error

    async def scrape(self):
        if self._error is not None:
            raise self._error
        return self._events


@pytest.fixture
def scrape(session, monkeypatch):
    """Run a real scrape_venue pass pretending the venue's page listed `events`,
    observed on Triangle calendar day `on_day`."""

    async def _scrape(venue, events, *, on_day, error=None):
        monkeypatch.setattr(ScrapeManager, "_get_scraper", lambda self, v: _StubScraper(events, error))
        monkeypatch.setattr("app.scrapers.manager.today_in_triangle", lambda: on_day)
        return await ScrapeManager(session).scrape_venue(venue)

    return _scrape


async def _events_by_artist(session) -> dict[str, Event]:
    result = await session.execute(select(Event))
    return {e.artist: e for e in result.scalars().all()}


async def test_event_missing_on_two_distinct_days_is_tombstoned(session, make_venue, scrape):
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    result = await scrape(venue, [keeper, vanished], on_day=DAY1)
    assert result["status"] == "success"
    # Delisted across two distinct scrape days -> tombstone.
    await scrape(venue, [keeper], on_day=DAY2)
    await scrape(venue, [keeper], on_day=DAY3)

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is not None
    assert rows["Juana Molina"].removed_at is None
    # Observation, not interpretation: delisting never infers cancellation.
    assert rows["Jessica Pratt"].status == "on_sale"


async def test_single_miss_does_not_tombstone(session, make_venue, scrape):
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, vanished], on_day=DAY1)
    await scrape(venue, [keeper], on_day=DAY2)

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is None


async def test_reappearance_resets_the_miss_streak(session, make_venue, scrape):
    """Misses must be consecutive: miss, reappear, miss is two interleaved gaps,
    not a delisting."""
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    flapper = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, flapper], on_day=DAY1)
    await scrape(venue, [keeper], on_day=DAY2)           # miss 1
    await scrape(venue, [keeper, flapper], on_day=DAY2)  # reappears -> streak resets
    await scrape(venue, [keeper], on_day=DAY3)           # miss 1 again, not miss 2

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is None


async def test_reappearance_clears_the_tombstone_and_truly_resets(session, make_venue, scrape):
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    relisted = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, relisted], on_day=DAY1)
    await scrape(venue, [keeper], on_day=DAY2)
    await scrape(venue, [keeper], on_day=DAY3)
    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is not None  # tombstoned...

    await scrape(venue, [keeper, relisted], on_day=DAY3)
    await session.refresh(rows["Jessica Pratt"])
    assert rows["Jessica Pratt"].removed_at is None      # ...relisted -> cleared

    # The streak reset with it: one further miss is not enough to re-tombstone,
    # two on distinct days are.
    await scrape(venue, [keeper], on_day=DAY3 + timedelta(days=1))
    await session.refresh(rows["Jessica Pratt"])
    assert rows["Jessica Pratt"].removed_at is None
    await scrape(venue, [keeper], on_day=DAY3 + timedelta(days=2))
    await session.refresh(rows["Jessica Pratt"])
    assert rows["Jessica Pratt"].removed_at is not None


async def test_misses_within_one_day_do_not_tombstone(session, make_venue, scrape):
    """Indie venues scrape 3x/day: a single degraded-but-nonzero day (bot challenge,
    half-rendered listing) must never tombstone — at most one miss per calendar day."""
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, vanished], on_day=DAY1)
    # Three delisted observations, all on the same calendar day.
    await scrape(venue, [keeper], on_day=DAY2)
    await scrape(venue, [keeper], on_day=DAY2)
    await scrape(venue, [keeper], on_day=DAY2)

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is None


async def test_failed_scrape_counts_for_nothing(session, make_venue, scrape):
    """One flaky night must not tombstone a venue's whole calendar."""
    venue = await make_venue()
    show = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [show], on_day=DAY1)
    # Two consecutive days of scraper failure: no snapshot, no misses.
    assert (await scrape(venue, [], on_day=DAY2, error=RuntimeError("bot challenge")))["status"] == "failed"
    assert (await scrape(venue, [], on_day=DAY3, error=RuntimeError("bot challenge")))["status"] == "failed"

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is None
    assert (await session.execute(select(EventMissState))).scalars().all() == []


async def test_zero_event_scrape_counts_for_nothing(session, make_venue, scrape):
    """The manager logs 'success' for a zero-event scrape, but an empty page from an
    active venue almost certainly means a broken scraper, not a mass cancellation."""
    venue = await make_venue()
    show = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [show], on_day=DAY1)
    assert (await scrape(venue, [], on_day=DAY2))["status"] == "success"
    assert (await scrape(venue, [], on_day=DAY3))["status"] == "success"

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is None
    assert (await session.execute(select(EventMissState))).scalars().all() == []


async def test_event_beyond_snapshot_horizon_is_never_missed(session, make_venue, scrape):
    """Horizon guard: a snapshot only vouches for the window it can see. An event
    past the max date in the scrape (e.g. a missing later page) is unobserved,
    not missing."""
    venue = await make_venue()
    near = _listing(venue.slug, "Juana Molina", D)
    far = _listing(venue.slug, "Chuquimamani-Condori", D + timedelta(days=60))

    await scrape(venue, [near, far], on_day=DAY1)
    # Subsequent scrapes only reach as far as `near` — `far` falls outside the
    # visible window on both days and must accrue nothing.
    await scrape(venue, [near], on_day=DAY2)
    await scrape(venue, [near], on_day=DAY3)

    rows = await _events_by_artist(session)
    assert rows["Chuquimamani-Condori"].removed_at is None
    assert (await session.execute(select(EventMissState))).scalars().all() == []


async def test_event_on_the_horizon_date_is_never_missed(session, make_venue, scrape):
    """The horizon guard is exclusive: item-capped listings can cut mid-date, so the
    max-seen date itself is unvouched — an absent event sharing it is 'below the
    fold', not missing."""
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D)
    below_fold = _listing(venue.slug, "Jessica Pratt", D)  # same date as the keeper

    await scrape(venue, [keeper, below_fold], on_day=DAY1)
    await scrape(venue, [keeper], on_day=DAY2)
    await scrape(venue, [keeper], on_day=DAY3)

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is None
    assert (await session.execute(select(EventMissState))).scalars().all() == []


async def test_mass_disappearance_is_treated_as_scraper_breakage(session, make_venue, scrape):
    """A page that suddenly stops rendering most of a venue's calendar (while one
    far-future entry keeps the horizon wide) is a broken scraper, not a mass
    delisting — no misses recorded when most in-window events vanish at once."""
    venue = await make_venue()
    lineup = ["Stereolab", "Cat Power", "Nilüfer Yanya", "Csillagrablók",
              "Hermanos Gutiérrez", "Chuquimamani-Condori"]
    shows = [_listing(venue.slug, artist, D + timedelta(days=n)) for n, artist in enumerate(lineup)]
    far = _listing(venue.slug, "Juana Molina", D + timedelta(days=10))

    await scrape(venue, [*shows, far], on_day=DAY1)
    # Two days of a degraded page rendering only the far-future entry.
    await scrape(venue, [far], on_day=DAY2)
    await scrape(venue, [far], on_day=DAY3)

    rows = await _events_by_artist(session)
    assert all(rows[artist].removed_at is None for artist in lineup)
    assert (await session.execute(select(EventMissState))).scalars().all() == []


async def test_multiple_events_vanishing_together_all_tombstone(session, make_venue, scrape):
    """A minority-sized batch of simultaneous delistings (below the mass guard)
    records a first miss for every vanished event in one scrape — the multi-row
    insert path — and each tombstones after the second day."""
    venue = await make_venue()
    lineup = ["Stereolab", "Cat Power", "Nilüfer Yanya", "Csillagrablók", "Hermanos Gutiérrez"]
    shows = [_listing(venue.slug, artist, D + timedelta(days=n)) for n, artist in enumerate(lineup)]
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=10))

    await scrape(venue, [*shows, keeper], on_day=DAY1)
    # Two shows delisted at once: 2 of 5 live in-window events is a minority and
    # below the absolute floor, so the mass guard must not swallow it.
    remaining = [*shows[2:], keeper]
    await scrape(venue, remaining, on_day=DAY2)
    assert len((await session.execute(select(EventMissState))).scalars().all()) == 2
    await scrape(venue, remaining, on_day=DAY3)

    rows = await _events_by_artist(session)
    assert rows["Stereolab"].removed_at is not None
    assert rows["Cat Power"].removed_at is not None
    assert all(rows[a].removed_at is None for a in lineup[2:])


async def test_tombstone_backlog_does_not_suppress_new_detections(session, make_venue, make_event, scrape):
    """Already-tombstoned events are expected to be absent — they must not count as
    disappearance evidence, or a venue with a tombstone backlog would trip the
    mass-disappearance guard on every scrape and never tombstone anything again."""
    venue = await make_venue()
    # A backlog of six previously delisted (tombstoned) future events.
    for n, artist in enumerate(["Stereolab", "Cat Power", "Nilüfer Yanya", "Csillagrablók",
                                "Hermanos Gutiérrez", "Chuquimamani-Condori"]):
        await make_event(venue=venue, artist=artist, date=D + timedelta(days=n),
                         removed_at=datetime.utcnow())
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=10))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, vanished], on_day=DAY1)
    # The venue delists one live show; the six tombstones must not drown it out.
    await scrape(venue, [keeper], on_day=DAY2)
    await scrape(venue, [keeper], on_day=DAY3)

    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is not None


async def test_stale_miss_streak_expires(session, make_venue, scrape):
    """Two misses separated by a long unobserved gap are two isolated glitches, not
    a delisting: a streak with no observation for over a week restarts instead of
    supplying the tombstoning second miss."""
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, vanished], on_day=DAY1)
    await scrape(venue, [keeper], on_day=DAY2)                      # miss 1
    # Next observed miss comes 10 days later: stale streak restarts.
    await scrape(venue, [keeper], on_day=DAY2 + timedelta(days=10))
    rows = await _events_by_artist(session)
    assert rows["Jessica Pratt"].removed_at is None
    # A consecutive follow-up miss the day after does tombstone.
    await scrape(venue, [keeper], on_day=DAY2 + timedelta(days=11))
    await session.refresh(rows["Jessica Pratt"])
    assert rows["Jessica Pratt"].removed_at is not None


async def test_scrape_result_reports_tombstoned_and_relisted(session, make_venue, scrape):
    """Tombstone stamps and relist clears are client-visible changes — the scrape
    result (and log) must account for them, not just upsert field changes."""
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, vanished], on_day=DAY1)
    r_miss = await scrape(venue, [keeper], on_day=DAY2)
    assert (r_miss["tombstoned"], r_miss["relisted"]) == (0, 0)
    r_stamp = await scrape(venue, [keeper], on_day=DAY3)
    assert (r_stamp["tombstoned"], r_stamp["relisted"]) == (1, 0)
    r_relist = await scrape(venue, [keeper, vanished], on_day=DAY3)
    assert (r_relist["tombstoned"], r_relist["relisted"]) == (0, 1)


async def test_updated_at_moves_only_on_client_visible_changes(session, make_venue, scrape):
    """updated_at is the downstream sync cursor: miss bookkeeping must not touch it;
    setting or clearing the tombstone must."""
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, vanished], on_day=DAY1)
    rows = await _events_by_artist(session)
    baseline = rows["Jessica Pratt"].updated_at

    # Miss 1 does not tombstone -> no client-visible change -> cursor unmoved.
    await scrape(venue, [keeper], on_day=DAY2)
    await session.refresh(rows["Jessica Pratt"])
    assert rows["Jessica Pratt"].updated_at == baseline

    # Miss 2 stamps removed_at -> cursor moves.
    await scrape(venue, [keeper], on_day=DAY3)
    await session.refresh(rows["Jessica Pratt"])
    tombstoned_at = rows["Jessica Pratt"].updated_at
    assert tombstoned_at > baseline

    # Reappearance clears removed_at -> cursor moves again.
    await scrape(venue, [keeper, vanished], on_day=DAY3)
    await session.refresh(rows["Jessica Pratt"])
    assert rows["Jessica Pratt"].updated_at > tombstoned_at


async def test_past_event_cleanup_survives_miss_state(session, make_venue, scrape, _sessionmaker, monkeypatch):
    """The 7-day cleanup deletes via Core delete(Event), which bypasses ORM cascades —
    the miss-state FK must cascade at the database level or the nightly job throws.

    The job's sessionmaker is monkeypatched to the test's: the module-global engine's
    pool is bound to another event loop (the same seam works for every scheduler job).
    The backdate uses the job's own clock (utcnow) so a UTC-ahead host near midnight
    can't land the date exactly on the strict cutoff.
    """
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    venue = await make_venue()
    keeper = _listing(venue.slug, "Juana Molina", D + timedelta(days=1))
    vanished = _listing(venue.slug, "Jessica Pratt", D)

    await scrape(venue, [keeper, vanished], on_day=DAY1)
    await scrape(venue, [keeper], on_day=DAY2)  # miss state now exists
    assert len((await session.execute(select(EventMissState))).scalars().all()) == 1

    # Simulate the show date passing beyond the cleanup buffer.
    rows = await _events_by_artist(session)
    rows["Jessica Pratt"].date = datetime.utcnow().date() - timedelta(days=8)
    await session.commit()

    await cleanup_past_events_job()

    remaining = (await session.execute(select(Event))).scalars().all()
    assert [e.artist for e in remaining] == ["Juana Molina"]
    assert (await session.execute(select(EventMissState))).scalars().all() == []
