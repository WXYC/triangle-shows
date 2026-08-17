"""
The ScrapeLog fetch behind every scrape-health verdict.

Role: the database half of scrape-health, shared by both callers of the pure evaluator
in ``app.services.scrape_health`` — ``GET /api/v1/health/scrapers`` (``app/api/v1.py``)
and the daily digest job (``app/scheduler.py``). Both need the identical two-query shape
(a capped recent fetch, plus the baseline row that fetch cannot promise to reach), and
the row cap and baseline window have to agree between them: the two surfaces answer the
same question about the same venue, so an operator reading "ok" off the endpoint and
"BROKE" out of the digest an hour later has no way to tell which one is wrong. One
implementation is how they agree — issue #86 part 1's review already found a real defect
in exactly the cap/baseline interaction below, and a second copy would be a second place
for that class of bug to live on uncorrected.

Neither caller can just import the other: ``app/scheduler.py`` imports ``app.database``,
``app.models`` and ``app.scrapers.manager``, so importing it from ``app/api/v1.py`` would
drag the whole scraper stack into the request path, and importing the API layer into the
scheduler inverts the same one-way rule (see ``app/cadence.py``'s docstring — the leaf
module extracted for this reason in part 1). A leaf both can depend on is the shape that
satisfies the rule without duplicating the query; this module is that leaf, sibling of
``app.services.events_query``, which is likewise the one place every read surface goes
for its shared query.

``app.services.scrape_health`` itself is deliberately not that home: it is duck-typed and
database-free on purpose, so the evaluator stays unit-testable on plain dataclasses with
no session, no engine, and no SQLAlchemy import at all.

Requires: an async SQLAlchemy session (``app.database``), ``app.models.ScrapeLog``,
``BASELINE_WINDOW_DAYS`` (``app.services.scrape_health``), ``CRON_HOURS`` (``app.cadence``).
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cadence import CRON_HOURS
from app.models import ScrapeLog
from app.services.scrape_health import BASELINE_WINDOW_DAYS

# --- Row cap for the per-venue recent fetch ---

# Derived from the cadence table rather than hand-set, so it can't become a second copy of
# cadence knowledge -- the exact drift app/cadence.py exists to prevent, where adding one
# hour to CRON_HOURS["indie"] would quietly make a fixed literal the binding constraint on
# how far back the evaluator can see.
#
# The cap deliberately does NOT carry the silent-zero baseline guard: no row count can,
# since unscheduled attempts are unbounded (see the baseline query below). What it does
# bound is how far back the recency signals and last_success_at can reach, which degrades
# gracefully -- a too-small cap costs a last_success_at timestamp, not a flipped verdict.
#
# SCRAPE_LOG_FETCH_HEADROOM covers attempts the cron table doesn't predict -- the startup
# scrape on every redeploy, and manual POST /api/scrape triggers, both of which write
# ScrapeLog rows like any other attempt.
SCRAPE_LOG_FETCH_HEADROOM = 2
_MAX_SCHEDULED_RUNS_PER_DAY = max(len(hours) for hours in CRON_HOURS.values())
RECENT_SCRAPE_LOG_LIMIT = (
    BASELINE_WINDOW_DAYS * _MAX_SCHEDULED_RUNS_PER_DAY * SCRAPE_LOG_FETCH_HEADROOM
)


async def fetch_venue_scrape_logs(
    session: AsyncSession, venue_id: int, *, now: datetime
) -> list[ScrapeLog]:
    """The ScrapeLog rows ``evaluate_venue_health`` needs for one venue, as of ``now``.

    Two queries, not one, and the split is load-bearing.

    The first is recent history, ordered ``started_at`` DESC (matching the
    ``ix_scrape_logs_venue_id_started_at`` composite index) and capped at
    RECENT_SCRAPE_LOG_LIMIT rows with **no lower time bound**. That is what makes row 0
    always the true most recent attempt however old it is, so a venue silent for months
    still resolves to a real "stale"/"critical" verdict instead of misreporting "unknown";
    a ``started_at >= cutoff`` filter would return nothing at all for precisely the venue
    that most needs the alarm. The cap is a safety valve on result size, never the thing
    deciding which window the evaluator sees.

    The second fetches the venue's most recent nonzero-events success inside the baseline
    window directly, because the row cap cannot promise to reach it: unscheduled attempts
    are unbounded (RUN_STARTUP_SCRAPE writes a full round on every redeploy, and POST
    /api/scrape is unauthenticated), so any fixed row count can be pushed past by churn.
    It is fetched rather than inferred because the failure it prevents is the inverted
    one -- a truncated history reads as "this venue never shows events, so its zeros are
    normal" and reports ok for a venue sitting behind a fresh bot wall.

    ``now`` bounds both queries (``started_at <= now``) so callers that replay at an
    earlier instant, like the digest job, are handed a row set the evaluator's own
    ``started_at > now`` exclusion can work over consistently.
    """
    logs = list(
        (
            await session.execute(
                select(ScrapeLog)
                .where(ScrapeLog.venue_id == venue_id, ScrapeLog.started_at <= now)
                .order_by(ScrapeLog.started_at.desc())
                .limit(RECENT_SCRAPE_LOG_LIMIT)
            )
        ).scalars().all()
    )

    baseline_cutoff = now - timedelta(days=BASELINE_WINDOW_DAYS)
    baseline_row = (
        await session.execute(
            select(ScrapeLog)
            .where(
                ScrapeLog.venue_id == venue_id,
                ScrapeLog.status == "success",
                ScrapeLog.events_found > 0,
                ScrapeLog.started_at >= baseline_cutoff,
                ScrapeLog.started_at <= now,
            )
            .order_by(ScrapeLog.started_at.desc())
            .limit(1)
        )
    ).scalars().first()
    # Identity, not equality: both queries run in one session, so SQLAlchemy's identity
    # map returns the same object for a row the capped fetch already had. Appending a
    # duplicate would corrupt the consecutive-window slice.
    if baseline_row is not None and not any(row is baseline_row for row in logs):
        logs.append(baseline_row)
    return logs
