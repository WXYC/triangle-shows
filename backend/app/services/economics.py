"""
Monthly unit-economics rollup: what the scrapers cost, what anyone consumed, what is
on the shelf.

Role: The query layer behind ``tools/unit_economics_report.py``. Every number the
monthly ledger in ``docs/economics/`` carries is computed here, so it can be tested
against a real database in the pytest harness rather than eyeballed in CLI output.
Read-only — nothing in this module writes.

Three tables answer three questions:

* ``scrape_logs`` — per-venue attempts, failures, and wall-clock time. The ops cost of
  keeping a metro's calendar current.
* ``feed_fetches`` — iCal subscriptions actually served, by distinct client. The
  demand signal the unit-economics experiment (#87) pre-registered a threshold against.
* ``events`` — live upcoming inventory and what the month added to it.

Two things here are easy to get subtly wrong and are therefore explicit:

**Month boundaries are market-time, not UTC.** Production runs in UTC, which rolls over
mid-evening Eastern, so a UTC month boundary moves the last few hours of every month
into the next one. Windows are computed as market-time midnights and converted to the
naive-UTC values the columns actually store, and per-day bucketing converts back the
same way. The two bounds of a month spanning a DST transition do not share an offset,
so neither can be derived from the other by a fixed delta.

**A feed section of zeros is ambiguous.** ``record_feed_fetch`` no-ops entirely when
``TELEMETRY_SALT`` is empty, so "no rows" means either "nobody subscribed" or "nothing
was ever recorded". ``FeedStats`` carries the salt's configured state and the window's
observed coverage alongside the counts so a caller can tell those apart; conflating
them would evaluate #87's threshold against a number that means "unplugged".

Requires: app.models, app.market_time (the configured region's zone), app.config
(TELEMETRY_SALT), an AsyncSession bound to PostgreSQL.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.market_time import market_tz, today_in_market
from app.models import Event, FeedFetch, ScrapeLog, ScrapeStatus, Venue

# The trailing window, ending at month end, over which a venue that keeps scraping
# successfully and keeps finding nothing is called out. Coarse by design: it is a
# single predicate standing in for real health detection until #86's verdicts land,
# at which point this report consumes those and the predicate goes away.
ZERO_EVENT_STREAK_DAYS = 7

# The rolling audience window #87 evaluates its distinct-client threshold over.
TRAILING_CLIENT_WINDOW_DAYS = 28

MONTH_FORMAT = "YYYY-MM"


# --- Month windows -----------------------------------------------------------------


def parse_month(value: str) -> date:
    """Parse a ``YYYY-MM`` month selector into the first of that month.

    Raises ``ValueError`` with a message naming the expected format — an operator
    reading it in a CLI error should not need to open this file.
    """
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except (ValueError, TypeError):
        raise ValueError(f"--month must be {MONTH_FORMAT} (got {value!r})") from None
    return parsed.date().replace(day=1)


def last_full_month(today: Optional[date] = None) -> date:
    """The first of the most recent *completed* calendar month, in market time.

    The default report month. The current month is deliberately never the default: a
    partial month's totals are not comparable to a full one's, and the ledger's whole
    purpose is month-over-month comparison.
    """
    today = today or today_in_market()
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def month_window(month: date) -> tuple[datetime, datetime]:
    """``[start, end)`` for a market-time calendar month, as naive UTC datetimes.

    The timestamp columns (``started_at``, ``fetched_at``, ``created_at``) are naive
    ``DateTime`` holding UTC, so the bounds must be expressed the same way to compare
    against them. Both ends are converted independently because a month containing a
    DST transition starts and ends at different UTC offsets.
    """
    tz = market_tz()
    start_local = datetime(month.year, month.month, 1, tzinfo=tz)
    if month.month == 12:
        end_local = datetime(month.year + 1, 1, 1, tzinfo=tz)
    else:
        end_local = datetime(month.year, month.month + 1, 1, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _market_date(column):
    """SQL expression turning a naive-UTC timestamp column into its market calendar date.

    ``timezone('UTC', ts)`` reads the naive value as UTC; the outer ``timezone(zone, ...)``
    renders it as local wall-clock; ``date()`` takes the day off that. Counting distinct
    days any other way buckets the evening of one market day onto the next.
    """
    return func.date(func.timezone(market_tz().key, func.timezone("UTC", column)))


# --- Result shapes -----------------------------------------------------------------


@dataclass(frozen=True)
class VenueScrapeStats:
    """One venue's scrape activity within the report month.

    A venue with no attempts is still reported (``attempts == 0``): a venue that
    silently dropped out of the rotation is a finding, not an absence. ``success_rate``
    is ``None`` rather than ``0.0`` in that case — there was nothing to succeed at, and
    a 0% row would read as total failure.

    ``successes + failures`` can be less than ``attempts``: a row left at ``running``
    by a process that died mid-scrape never reaches a terminal status. Those rows count
    as attempts and drag the success rate down, which is the honest reading — the
    scrape was paid for and produced nothing.
    """

    venue_slug: str
    venue_name: str
    attempts: int
    successes: int
    failures: int
    success_rate: Optional[float]
    mean_duration_seconds: Optional[float]
    total_duration_seconds: float
    last_events_found: Optional[int]
    zero_event_streak: bool


@dataclass(frozen=True)
class FeedStats:
    """iCal feed demand within the report month.

    ``salt_configured`` and ``has_rows`` exist so a caller can separate "nobody
    subscribed" from "the instrumentation was never switched on"; the counts alone
    cannot.
    """

    total_fetches: int
    distinct_clients: int
    trailing_28d_distinct_clients: int
    full_feed_fetches: int
    filtered_fetches: int
    per_venue_fetches: dict[str, int]
    first_fetch_at: Optional[datetime]
    last_fetch_at: Optional[datetime]
    days_with_rows: int
    days_in_window: int
    salt_configured: bool

    @property
    def has_rows(self) -> bool:
        return self.total_fetches > 0


@dataclass(frozen=True)
class InventoryStats:
    """What is on the shelf, and what the month put there.

    ``live_upcoming_events`` is a point-in-time count as of *now*, not as of the report
    month — it answers "what does a visitor see today". The creation counts are the
    month's own output, split so the tombstoned rows are visible rather than quietly
    dropped from a number labelled "created".
    """

    live_upcoming_events: int
    events_created_in_month: int
    events_created_in_month_tombstoned: int


@dataclass(frozen=True)
class MonthReport:
    """Everything the monthly ledger needs, plus the window it was computed over."""

    month: date
    window_start: datetime
    window_end: datetime
    timezone_name: str
    venues: list[VenueScrapeStats]
    feed: FeedStats
    inventory: InventoryStats


# --- Queries -----------------------------------------------------------------------


async def venue_scrape_stats(session: AsyncSession, month: date) -> list[VenueScrapeStats]:
    """Per-venue scrape rollup for the month, one row per venue, ordered by slug."""
    start, end = month_window(month)
    success = ScrapeStatus.success.value
    failed = ScrapeStatus.failed.value

    in_window = (ScrapeLog.started_at >= start, ScrapeLog.started_at < end)

    totals = (
        await session.execute(
            select(
                ScrapeLog.venue_id,
                func.count().label("attempts"),
                func.count().filter(ScrapeLog.status == success).label("successes"),
                func.count().filter(ScrapeLog.status == failed).label("failures"),
                func.coalesce(func.sum(ScrapeLog.duration_seconds), 0.0).label("total_duration"),
                func.avg(ScrapeLog.duration_seconds).label("mean_duration"),
            )
            .where(*in_window)
            .group_by(ScrapeLog.venue_id)
        )
    ).all()
    by_venue = {row.venue_id: row for row in totals}

    # The streak window ends with the month, not with today: the report is a statement
    # about the month it names, and must not change when re-run later.
    streak_start = end - timedelta(days=ZERO_EVENT_STREAK_DAYS)
    streaks = (
        await session.execute(
            select(
                ScrapeLog.venue_id,
                func.count().label("successes"),
                func.max(ScrapeLog.events_found).label("max_found"),
            )
            .where(
                ScrapeLog.status == success,
                ScrapeLog.started_at >= streak_start,
                ScrapeLog.started_at < end,
            )
            .group_by(ScrapeLog.venue_id)
        )
    ).all()
    streak_by_venue = {row.venue_id: row for row in streaks}

    # Most recent *successful* scrape in the window. A failed run's events_found is 0
    # by construction, which would read as "found nothing" rather than "did not look".
    latest = (
        await session.execute(
            select(ScrapeLog.venue_id, ScrapeLog.events_found)
            .where(ScrapeLog.status == success, *in_window)
            .distinct(ScrapeLog.venue_id)
            .order_by(ScrapeLog.venue_id, ScrapeLog.started_at.desc())
        )
    ).all()
    last_found_by_venue = {row.venue_id: row.events_found for row in latest}

    venues = (await session.execute(select(Venue).order_by(Venue.slug))).scalars().all()

    stats: list[VenueScrapeStats] = []
    for venue in venues:
        totals_row = by_venue.get(venue.id)
        attempts = totals_row.attempts if totals_row else 0
        successes = totals_row.successes if totals_row else 0
        failures = totals_row.failures if totals_row else 0
        streak_row = streak_by_venue.get(venue.id)
        stats.append(
            VenueScrapeStats(
                venue_slug=venue.slug,
                venue_name=venue.name,
                attempts=attempts,
                successes=successes,
                failures=failures,
                success_rate=(successes / attempts) if attempts else None,
                mean_duration_seconds=(
                    float(totals_row.mean_duration)
                    if totals_row and totals_row.mean_duration is not None
                    else None
                ),
                total_duration_seconds=float(totals_row.total_duration) if totals_row else 0.0,
                last_events_found=last_found_by_venue.get(venue.id),
                # Still scraping (at least one success in the trailing window) and still
                # finding nothing (none of those successes found anything).
                zero_event_streak=bool(
                    streak_row and streak_row.successes > 0 and streak_row.max_found == 0
                ),
            )
        )
    return stats


async def feed_stats(session: AsyncSession, month: date) -> FeedStats:
    """iCal feed demand for the month, plus enough context to interpret a zero."""
    start, end = month_window(month)
    in_window = (FeedFetch.fetched_at >= start, FeedFetch.fetched_at < end)

    row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count(func.distinct(FeedFetch.client_hash)).label("clients"),
                func.count().filter(FeedFetch.venue_filter.is_(None)).label("full_feed"),
                func.count().filter(FeedFetch.venue_filter.isnot(None)).label("filtered"),
                func.min(FeedFetch.fetched_at).label("first_at"),
                func.max(FeedFetch.fetched_at).label("last_at"),
                func.count(func.distinct(_market_date(FeedFetch.fetched_at))).label("days"),
            ).where(*in_window)
        )
    ).one()

    trailing_start = end - timedelta(days=TRAILING_CLIENT_WINDOW_DAYS)
    trailing_clients = (
        await session.execute(
            select(func.count(func.distinct(FeedFetch.client_hash))).where(
                FeedFetch.fetched_at >= trailing_start, FeedFetch.fetched_at < end
            )
        )
    ).scalar_one()

    # venue_filter holds the normalized, sorted, comma-joined slug set, so a two-venue
    # subscription is one row that must count toward both venues. Grouping in SQL and
    # splitting here keeps that explicit; comparing the column whole would file the row
    # under a venue named "cats-cradle,motorco".
    filter_rows = (
        await session.execute(
            select(FeedFetch.venue_filter, func.count().label("n"))
            .where(*in_window, FeedFetch.venue_filter.isnot(None))
            .group_by(FeedFetch.venue_filter)
        )
    ).all()
    per_venue: dict[str, int] = {}
    for filter_row in filter_rows:
        for slug in filter_row.venue_filter.split(","):
            slug = slug.strip()
            if slug:
                per_venue[slug] = per_venue.get(slug, 0) + filter_row.n

    return FeedStats(
        total_fetches=row.total,
        distinct_clients=row.clients,
        trailing_28d_distinct_clients=trailing_clients,
        full_feed_fetches=row.full_feed,
        filtered_fetches=row.filtered,
        per_venue_fetches=dict(sorted(per_venue.items())),
        first_fetch_at=row.first_at,
        last_fetch_at=row.last_at,
        days_with_rows=row.days,
        days_in_window=(end - start).days,
        salt_configured=bool(settings.TELEMETRY_SALT),
    )


async def inventory_stats(session: AsyncSession, month: date) -> InventoryStats:
    """Live inventory now, and what the report month contributed to it."""
    start, end = month_window(month)

    live_upcoming = (
        await session.execute(
            select(func.count())
            .select_from(Event)
            .where(Event.removed_at.is_(None), Event.date >= today_in_market())
        )
    ).scalar_one()

    created = (
        await session.execute(
            select(
                func.count().filter(Event.removed_at.is_(None)).label("kept"),
                func.count().filter(Event.removed_at.isnot(None)).label("tombstoned"),
            )
            .select_from(Event)
            .where(Event.created_at >= start, Event.created_at < end)
        )
    ).one()

    return InventoryStats(
        live_upcoming_events=live_upcoming,
        events_created_in_month=created.kept,
        events_created_in_month_tombstoned=created.tombstoned,
    )


async def collect_month_report(session: AsyncSession, month: date) -> MonthReport:
    """Assemble the whole month's rollup. Read-only; safe against production."""
    start, end = month_window(month)
    return MonthReport(
        month=month,
        window_start=start,
        window_end=end,
        timezone_name=market_tz().key,
        venues=await venue_scrape_stats(session, month),
        feed=await feed_stats(session, month),
        inventory=await inventory_stats(session, month),
    )
