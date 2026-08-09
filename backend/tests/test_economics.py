"""
Tests for the monthly unit-economics rollup (app/services/economics.py) and the
markdown it feeds (tools/unit_economics_report.py).

The rollup answers the unit-economics experiment's questions from data the app already
writes — ``scrape_logs`` for what the scrapers cost, ``feed_fetches`` for what anyone
actually consumed, ``events`` for what is on the shelf. The load-bearing cases are the
ones where a plausible-looking number would mean something other than what it says:

* a month boundary read in UTC instead of market time silently moves the last evening
  of the month into the next one;
* a feed section of zeros means "nobody subscribed" *or* "telemetry was never turned
  on", and the experiment's pre-registered threshold is meaningless against the second;
* a venue still scraping successfully but finding nothing looks healthy in every
  aggregate until someone asks whether it found anything.
"""

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.models import Event, FeedFetch, ScrapeLog, ScrapeStatus
from app.services import economics

MARKET_TZ = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cli_module():
    """Import tools/unit_economics_report.py by path.

    It lives outside the backend package (operators look for ops scripts in tools/,
    and its README indexes them there), so it is not importable by name from the test
    suite. Loading it by path keeps its markdown rendering under test anyway — the
    "paste-ready output" acceptance criterion is about that function, not about the
    queries beneath it.
    """
    spec = importlib.util.spec_from_file_location(
        "unit_economics_report", REPO_ROOT / "tools" / "unit_economics_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _utc(local: datetime) -> datetime:
    """A market-time wall clock as the naive-UTC value the database actually stores."""
    return local.replace(tzinfo=MARKET_TZ).astimezone(timezone.utc).replace(tzinfo=None)


# --- Month windows -----------------------------------------------------------------


def test_parse_month_accepts_yyyy_mm_and_normalizes_to_the_first():
    assert economics.parse_month("2026-07") == date(2026, 7, 1)


@pytest.mark.parametrize(
    "bad", ["2026", "2026-13", "july", "2026-07-15", "", "2026/07"]
)
def test_parse_month_rejects_anything_else_with_a_usable_message(bad):
    with pytest.raises(ValueError) as excinfo:
        economics.parse_month(bad)
    # The operator has to be able to fix the invocation from the message alone.
    assert "YYYY-MM" in str(excinfo.value)


def test_last_full_month_is_the_month_before_the_current_market_month():
    assert economics.last_full_month(today=date(2026, 8, 9)) == date(2026, 7, 1)
    # January rolls the year back rather than producing month 0.
    assert economics.last_full_month(today=date(2026, 1, 3)) == date(2025, 12, 1)


def test_month_window_bounds_are_market_time_midnights_expressed_in_utc():
    start, end = economics.month_window(date(2026, 7, 1))
    # July 2026 is EDT (UTC-4): local midnight is 04:00 UTC.
    assert start == datetime(2026, 7, 1, 4, 0)
    assert end == datetime(2026, 8, 1, 4, 0)


def test_month_window_crosses_the_dst_boundary_without_drifting():
    # November 2026: the month starts in EDT (UTC-4) and ends in EST (UTC-5), so the
    # two bounds do not share an offset. Computing either from a fixed offset is wrong.
    start, end = economics.month_window(date(2026, 11, 1))
    assert start == datetime(2026, 11, 1, 4, 0)
    assert end == datetime(2026, 12, 1, 5, 0)


# --- Per-venue scrape stats --------------------------------------------------------


async def test_venue_scrape_stats_rolls_up_attempts_failures_and_durations(
    session, make_venue
):
    venue = await make_venue(slug="cats-cradle", name="Cat's Cradle")
    july = date(2026, 7, 1)
    for started, status, found, duration in [
        (datetime(2026, 7, 2, 6, 0), ScrapeStatus.success, 12, 4.0),
        (datetime(2026, 7, 9, 6, 0), ScrapeStatus.success, 9, 6.0),
        (datetime(2026, 7, 16, 6, 0), ScrapeStatus.failed, 0, 2.0),
        # Outside the window (June 30 in market time) — must not be counted.
        (datetime(2026, 6, 30, 20, 0), ScrapeStatus.success, 99, 99.0),
    ]:
        session.add(
            ScrapeLog(
                venue_id=venue.id,
                scraper_type="manual",
                started_at=_utc(started),
                status=status.value,
                events_found=found,
                duration_seconds=duration,
            )
        )
    await session.commit()

    stats = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, july)}
    cradle = stats["cats-cradle"]
    assert cradle.attempts == 3
    assert cradle.failures == 1
    assert cradle.success_rate == pytest.approx(2 / 3)
    assert cradle.total_duration_seconds == pytest.approx(12.0)
    assert cradle.mean_duration_seconds == pytest.approx(4.0)
    # The most recent attempt in the window is the July 16 *failure*, whose events_found
    # is 0 by construction. Reporting that would say the venue found nothing when in
    # fact it did not look — so the figure comes from the last successful scrape.
    assert cradle.last_events_found == 9


async def test_a_late_evening_scrape_belongs_to_the_market_month_it_happened_in(
    session, make_venue
):
    """23:30 ET on the last day of June is 03:30 UTC on July 1.

    Read in UTC it lands in July; read in market time — the only reading that matches
    how anyone talks about "June" here — it stays in June.
    """
    venue = await make_venue(slug="motorco", name="Motorco")
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type="manual",
            started_at=_utc(datetime(2026, 6, 30, 23, 30)),
            status=ScrapeStatus.success.value,
            events_found=3,
            duration_seconds=1.0,
        )
    )
    await session.commit()

    june = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, date(2026, 6, 1))}
    july = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, date(2026, 7, 1))}
    assert june["motorco"].attempts == 1
    assert july["motorco"].attempts == 0


async def test_venues_never_scraped_in_the_window_still_appear_with_zero_attempts(
    session, make_venue
):
    """A venue that silently stopped being scraped is the finding, not an absence."""
    await make_venue(slug="the-pinhook", name="The Pinhook")
    stats = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, date(2026, 7, 1))}
    pinhook = stats["the-pinhook"]
    assert pinhook.attempts == 0
    assert pinhook.failures == 0
    assert pinhook.success_rate is None  # not 0.0 — nothing was attempted to succeed at
    assert pinhook.mean_duration_seconds is None
    assert pinhook.total_duration_seconds == 0.0
    assert pinhook.last_events_found is None
    assert pinhook.zero_event_streak is False


async def test_zero_event_streak_flags_a_venue_scraping_successfully_but_finding_nothing(
    session, make_venue
):
    venue = await make_venue(slug="local-506", name="Local 506")
    # Daily successful scrapes across the last week of July, all empty.
    for day in range(25, 32):
        session.add(
            ScrapeLog(
                venue_id=venue.id,
                scraper_type="manual",
                started_at=_utc(datetime(2026, 7, day, 6, 0)),
                status=ScrapeStatus.success.value,
                events_found=0,
                duration_seconds=1.0,
            )
        )
    await session.commit()

    stats = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, date(2026, 7, 1))}
    assert stats["local-506"].zero_event_streak is True
    assert stats["local-506"].last_events_found == 0


async def test_zero_event_streak_is_broken_by_a_single_non_zero_find(session, make_venue):
    venue = await make_venue(slug="kings", name="Kings")
    for day in range(25, 32):
        session.add(
            ScrapeLog(
                venue_id=venue.id,
                scraper_type="manual",
                started_at=_utc(datetime(2026, 7, day, 6, 0)),
                status=ScrapeStatus.success.value,
                # One productive scrape mid-week: the venue is fine, just quiet.
                events_found=4 if day == 28 else 0,
                duration_seconds=1.0,
            )
        )
    await session.commit()

    stats = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, date(2026, 7, 1))}
    assert stats["kings"].zero_event_streak is False


async def test_zero_event_streak_ignores_empty_finds_older_than_the_trailing_week(
    session, make_venue
):
    """The flag is evaluated at month end, not over the whole month.

    A venue that was empty early in July and productive at the end of it is healthy;
    counting the whole month would flag it.
    """
    venue = await make_venue(slug="cats-cradle-back-room", name="Cat's Cradle Back Room")
    for day in range(1, 10):
        session.add(
            ScrapeLog(
                venue_id=venue.id,
                scraper_type="manual",
                started_at=_utc(datetime(2026, 7, day, 6, 0)),
                status=ScrapeStatus.success.value,
                events_found=0,
                duration_seconds=1.0,
            )
        )
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type="manual",
            started_at=_utc(datetime(2026, 7, 30, 6, 0)),
            status=ScrapeStatus.success.value,
            events_found=7,
            duration_seconds=1.0,
        )
    )
    await session.commit()

    stats = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, date(2026, 7, 1))}
    assert stats["cats-cradle-back-room"].zero_event_streak is False
    assert stats["cats-cradle-back-room"].last_events_found == 7


async def test_zero_event_streak_needs_successful_scrapes_not_merely_an_absence_of_finds(
    session, make_venue
):
    """A venue whose scrapes are *failing* is a different problem with a different fix.

    Failures already show up as a success rate below 1; letting them also raise the
    silent-breakage flag would make the flag mean two things at once.
    """
    venue = await make_venue(slug="the-ritz", name="The Ritz")
    for day in range(25, 32):
        session.add(
            ScrapeLog(
                venue_id=venue.id,
                scraper_type="manual",
                started_at=_utc(datetime(2026, 7, day, 6, 0)),
                status=ScrapeStatus.failed.value,
                events_found=0,
                error_message="boom",
                duration_seconds=1.0,
            )
        )
    await session.commit()

    stats = {s.venue_slug: s for s in await economics.venue_scrape_stats(session, date(2026, 7, 1))}
    assert stats["the-ritz"].zero_event_streak is False
    assert stats["the-ritz"].failures == 7
    assert stats["the-ritz"].success_rate == 0.0


# --- Feed stats --------------------------------------------------------------------


async def _seed_feed_fetches(session, rows):
    for fetched_at, client_hash, venue_filter in rows:
        session.add(
            FeedFetch(
                fetched_at=_utc(fetched_at), client_hash=client_hash, venue_filter=venue_filter
            )
        )
    await session.commit()


async def test_feed_stats_counts_in_month_and_trailing_28_day_distinct_clients(session):
    await _seed_feed_fetches(
        session,
        [
            # Well before the trailing-28-day window, inside the month.
            (datetime(2026, 7, 2, 9, 0), "aaaaaaaaaaaaaaaa", None),
            (datetime(2026, 7, 3, 9, 0), "aaaaaaaaaaaaaaaa", None),
            # Inside both windows.
            (datetime(2026, 7, 20, 9, 0), "bbbbbbbbbbbbbbbb", None),
            (datetime(2026, 7, 29, 9, 0), "cccccccccccccccc", None),
            # After the month ends — in neither.
            (datetime(2026, 8, 4, 9, 0), "dddddddddddddddd", None),
        ],
    )

    stats = await economics.feed_stats(session, date(2026, 7, 1))
    assert stats.total_fetches == 4
    assert stats.distinct_clients == 3
    # Trailing 28 days ends with the month (Aug 1 local) and so starts July 4: the
    # July 2/3 client falls out, the August one was never in.
    assert stats.trailing_28d_distinct_clients == 2


async def test_feed_stats_splits_full_feed_from_filtered_on_null_not_string_equality(
    session,
):
    await _seed_feed_fetches(
        session,
        [
            (datetime(2026, 7, 5, 9, 0), "aaaaaaaaaaaaaaaa", None),
            (datetime(2026, 7, 6, 9, 0), "bbbbbbbbbbbbbbbb", None),
            (datetime(2026, 7, 7, 9, 0), "cccccccccccccccc", "cats-cradle"),
            (datetime(2026, 7, 8, 9, 0), "dddddddddddddddd", "cats-cradle,motorco"),
        ],
    )

    stats = await economics.feed_stats(session, date(2026, 7, 1))
    assert stats.full_feed_fetches == 2
    assert stats.filtered_fetches == 2


async def test_a_multi_venue_subscription_counts_toward_every_venue_in_it(session):
    """venue_filter stores the normalized, comma-joined, sorted slug set.

    Comparing it whole would file "cats-cradle,motorco" under a venue named
    "cats-cradle,motorco" — a venue that does not exist — and credit neither real one.
    """
    await _seed_feed_fetches(
        session,
        [
            (datetime(2026, 7, 5, 9, 0), "aaaaaaaaaaaaaaaa", "cats-cradle,motorco"),
            (datetime(2026, 7, 6, 9, 0), "bbbbbbbbbbbbbbbb", "motorco"),
            (datetime(2026, 7, 7, 9, 0), "cccccccccccccccc", None),
        ],
    )

    stats = await economics.feed_stats(session, date(2026, 7, 1))
    assert stats.per_venue_fetches == {"cats-cradle": 1, "motorco": 2}


async def test_feed_stats_reports_coverage_so_a_partial_month_is_not_read_as_a_full_one(
    session,
):
    # Both fetches happen on one market-time day, and 21:00 ET is 01:00 UTC the *next*
    # day — so bucketing by the stored UTC timestamp reports two days of activity where
    # there was one evening of it.
    await _seed_feed_fetches(
        session,
        [
            (datetime(2026, 7, 20, 9, 0), "aaaaaaaaaaaaaaaa", None),
            (datetime(2026, 7, 20, 21, 0), "bbbbbbbbbbbbbbbb", None),
        ],
    )

    stats = await economics.feed_stats(session, date(2026, 7, 1))
    assert stats.first_fetch_at == _utc(datetime(2026, 7, 20, 9, 0))
    assert stats.last_fetch_at == _utc(datetime(2026, 7, 20, 21, 0))
    assert stats.days_with_rows == 1
    assert stats.days_in_window == 31


async def test_feed_stats_reports_an_empty_window_as_empty_rather_than_as_zero_demand(
    session,
):
    stats = await economics.feed_stats(session, date(2026, 7, 1))
    assert stats.total_fetches == 0
    assert stats.distinct_clients == 0
    assert stats.days_with_rows == 0
    assert stats.first_fetch_at is None
    assert stats.last_fetch_at is None
    assert stats.has_rows is False


async def test_feed_stats_carries_whether_the_instrumentation_was_even_switched_on(
    session, monkeypatch
):
    """The salt is the on/off switch: record_feed_fetch no-ops when it is empty.

    Without this flag a report cannot tell "nobody subscribed" from "nothing was ever
    recorded", and #87's pre-registered threshold would be evaluated against the second.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TELEMETRY_SALT", "")
    assert (await economics.feed_stats(session, date(2026, 7, 1))).salt_configured is False

    monkeypatch.setattr(settings, "TELEMETRY_SALT", "a-real-salt")
    assert (await economics.feed_stats(session, date(2026, 7, 1))).salt_configured is True


# --- Inventory ---------------------------------------------------------------------


async def test_inventory_counts_live_upcoming_events_and_excludes_tombstoned_ones(
    session, make_venue, make_event
):
    venue = await make_venue(slug="haw-river", name="Haw River Ballroom")
    await make_event(venue=venue, name="Live One")
    await make_event(venue=venue, name="Live Two")
    await make_event(venue=venue, name="Vanished", removed_at=datetime(2026, 7, 15, 12, 0))

    stats = await economics.inventory_stats(session, date(2026, 7, 1))
    assert stats.live_upcoming_events == 2


async def test_inventory_counts_events_created_within_the_market_month(
    session, make_venue, make_event
):
    venue = await make_venue(slug="the-fruit", name="The Fruit")
    await make_event(venue=venue, name="July", created_at=_utc(datetime(2026, 7, 10, 9, 0)))
    # 23:30 ET on June 30 — UTC-side it is July, market-side it is June.
    await make_event(venue=venue, name="June", created_at=_utc(datetime(2026, 6, 30, 23, 30)))
    await make_event(venue=venue, name="August", created_at=_utc(datetime(2026, 8, 2, 9, 0)))

    stats = await economics.inventory_stats(session, date(2026, 7, 1))
    assert stats.events_created_in_month == 1


async def test_inventory_reports_tombstoned_creations_separately_rather_than_hiding_them(
    session, make_venue, make_event
):
    """Both numbers are useful and they answer different questions.

    Excluding tombstoned rows from the creation count without saying so would make a
    productive month look thin; including them silently would overstate what is on the
    shelf. So the excluded rows are counted, not dropped.
    """
    venue = await make_venue(slug="motorco", name="Motorco")
    created = _utc(datetime(2026, 7, 10, 9, 0))
    await make_event(venue=venue, name="Kept", created_at=created)
    await make_event(
        venue=venue, name="Gone", created_at=created, removed_at=datetime(2026, 7, 20, 9, 0)
    )

    stats = await economics.inventory_stats(session, date(2026, 7, 1))
    assert stats.events_created_in_month == 1
    assert stats.events_created_in_month_tombstoned == 1


# --- The assembled report and its markdown -----------------------------------------


async def test_collect_month_report_carries_the_window_it_actually_used(
    session, make_venue
):
    await make_venue(slug="cats-cradle", name="Cat's Cradle")
    report = await economics.collect_month_report(session, date(2026, 7, 1))
    assert report.month == date(2026, 7, 1)
    assert report.window_start == datetime(2026, 7, 1, 4, 0)
    assert report.window_end == datetime(2026, 8, 1, 4, 0)
    assert [v.venue_slug for v in report.venues] == ["cats-cradle"]


async def test_markdown_is_a_paste_ready_table_set(session, make_venue):
    venue = await make_venue(slug="cats-cradle", name="Cat's Cradle")
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type="manual",
            started_at=_utc(datetime(2026, 7, 2, 6, 0)),
            status=ScrapeStatus.success.value,
            events_found=12,
            duration_seconds=4.0,
        )
    )
    await session.commit()
    await _seed_feed_fetches(session, [(datetime(2026, 7, 5, 9, 0), "aaaaaaaaaaaaaaaa", None)])

    cli = _load_cli_module()
    markdown = cli.render_markdown(await economics.collect_month_report(session, date(2026, 7, 1)))

    assert "2026-07" in markdown
    # A markdown table, not a formatted-with-spaces console dump.
    assert "| --- |" in markdown or "|---" in markdown
    assert "cats-cradle" in markdown
    # Every table's header row and its separator must have the same column count, or it
    # renders as literal text wherever it is pasted.
    for line, following in zip(markdown.splitlines(), markdown.splitlines()[1:]):
        if line.startswith("|") and set(following.replace("|", "").strip()) <= {"-", " ", ":"} and following.startswith("|"):
            assert line.count("|") == following.count("|"), f"ragged table header: {line!r}"


async def test_markdown_says_telemetry_was_never_enabled_rather_than_printing_zero(
    session, make_venue, monkeypatch
):
    """The all-zeros month is the case that can mislead #87's threshold.

    "0 distinct clients" against an unset salt does not mean nobody subscribed; it
    means nothing was ever recorded. The two must not render identically.
    """
    from app.config import settings

    await make_venue(slug="cats-cradle", name="Cat's Cradle")
    monkeypatch.setattr(settings, "TELEMETRY_SALT", "")

    cli = _load_cli_module()
    markdown = cli.render_markdown(await economics.collect_month_report(session, date(2026, 7, 1)))

    lowered = markdown.lower()
    assert "not enabled" in lowered or "disabled" in lowered
    # And it must not present the threshold input as a measured zero.
    assert "0 distinct clients" not in lowered


async def test_markdown_distinguishes_enabled_but_unused_from_never_enabled(
    session, make_venue, monkeypatch
):
    """Salt set, no rows: that *is* a measured zero, and it should read as one."""
    from app.config import settings

    await make_venue(slug="cats-cradle", name="Cat's Cradle")
    monkeypatch.setattr(settings, "TELEMETRY_SALT", "a-real-salt")

    cli = _load_cli_module()
    markdown = cli.render_markdown(await economics.collect_month_report(session, date(2026, 7, 1)))

    assert "no rows" in markdown.lower()
    assert "not enabled" not in markdown.lower()
