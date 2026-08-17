"""
APScheduler job definitions for periodic scraping and data maintenance.

Role: Started during FastAPI app startup (main.py) when ENABLE_SCHEDULER=true.
      Runs scrape jobs on a fixed cron schedule as an alternative to Cloud Scheduler
      HTTP triggers — both ultimately call the same ScrapeManager logic. Also runs
      the daily scrape-health digest (issue #86 part 2), which reuses the pure
      evaluator from app.services.scrape_health rather than re-deriving verdicts.
Requires: ENABLE_SCHEDULER env var (via config.py), app.scrapers.manager.ScrapeManager,
          app.database.async_session, app.services.scrape_health, and a running
          async event loop (provided by FastAPI).
"""

# --- Imports ---
import logging
from datetime import datetime, timedelta

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select

from app.cadence import cron_hour_string
from app.database import async_session
from app.models import Event, Venue
from app.observability import report_error, send_alert
from app.scrapers.manager import ScrapeManager
from app.services.scrape_health import ScrapeHealthVerdict, evaluate_venue_health
from app.services.scrape_health_query import fetch_venue_scrape_logs
from app.site_config import load_site_config

# --- Module-level setup ---

logger = logging.getLogger(__name__)

# Singleton scheduler instance — started/stopped in main.py lifespan handler
scheduler = AsyncIOScheduler()


# --- Scheduled job callbacks ---

async def scrape_ticketmaster_job():
    """Scrape Ticketmaster venues."""
    logger.info("Starting scheduled Ticketmaster scrape")
    async with async_session() as session:
        manager = ScrapeManager(session)
        results = await manager.scrape_ticketmaster()
        for r in results:
            logger.info(f"  {r}")


async def scrape_indie_job():
    """Scrape indie venues."""
    logger.info("Starting scheduled indie venue scrape")
    async with async_session() as session:
        manager = ScrapeManager(session)
        results = await manager.scrape_indie()
        for r in results:
            logger.info(f"  {r}")


async def cleanup_past_events_job():
    """Delete events more than 7 days in the past.

    Tests must monkeypatch this module's ``async_session`` to their own sessionmaker:
    the module-global engine's connection pool is bound to the production event loop,
    and borrowing its pooled connections across per-test loops breaks.
    """
    logger.info("Cleaning up past events")
    # Keep a 7-day buffer so recently-ended events don't vanish immediately
    cutoff = datetime.utcnow().date() - timedelta(days=7)
    async with async_session() as session:
        result = await session.execute(
            delete(Event).where(Event.date < cutoff)
        )
        await session.commit()
        logger.info(f"Deleted {result.rowcount} past events")


# --- Scrape-health digest (issue #86 part 2) ---

def _format_digest(
    site_name: str, broke: list[ScrapeHealthVerdict], recovered: list[ScrapeHealthVerdict]
) -> str:
    """Render the transition list into the text posted (or logged) by send_alert.

    Prefixed with site.name (not site.title, the lowercase page-title slug) so
    Triangle and Seattle can share one ops channel and still tell their alerts
    apart. `broke` carries each venue's *current* verdict (what it broke into);
    `recovered` carries each venue's *previous* verdict (what it recovered
    from) -- the more useful half of a recovery notice is what had been wrong.
    """
    total = len(broke) + len(recovered)
    lines = [f"{site_name} scrape-health digest: {total} change(s)"]
    for v in broke:
        lines.append(f"BROKE: {v.venue_slug} [{v.status}/{v.signal}] {v.detail}")
    for v in recovered:
        lines.append(f"RECOVERED: {v.venue_slug} (was {v.status}/{v.signal})")
    return "\n".join(lines)


async def scrape_health_digest_job():
    """Daily transition-only scrape-health alert (issue #86 part 2).

    Runs at 7 AM market time, after the morning scrape wave, so the freshest
    results are already in ScrapeLog by the time this evaluates -- registered
    in configure_scheduler() below with the site-configured timezone, same as
    every other job.

    Stateless by design: APScheduler's default in-memory jobstore does not
    survive a Railway redeploy, so there is no durable "last digest ran at" to
    diff against. The previous verdict is instead derived by replaying
    evaluate_venue_health at ``now - 24h`` against the *same* fetched rows --
    a true replay, not an approximation, because the evaluator excludes any
    row with ``started_at > now`` by contract (pinned by a dedicated test in
    the detection PR). 24h is not an arbitrary choice either: it is this job's
    own cadence, so the replay always lines up with "as of yesterday's run".

    Only a venue whose broken/not-broken state actually changed between the
    two instants is reported -- a venue that has been broken for a week does
    not re-page every morning, and the ordinary case (nothing changed anywhere)
    sends nothing at all.
    """
    logger.info("Running scrape-health digest")
    now = datetime.utcnow()
    previous_now = now - timedelta(hours=24)
    site_name = load_site_config().site.name

    broke: list[ScrapeHealthVerdict] = []
    recovered: list[ScrapeHealthVerdict] = []
    async with async_session() as session:
        venues = (await session.execute(select(Venue).order_by(Venue.city, Venue.name))).scalars().all()

        for venue in venues:
            # The same fetch GET /api/v1/health/scrapers uses, from the shared leaf
            # module both import -- one capped recent slice plus the separately-fetched
            # 30-day baseline row. It is shared rather than reimplemented because the
            # row cap and the baseline window must agree between the endpoint and this
            # job: they answer the same question about the same venue, so two copies
            # free to drift would let one report ok while the other pages.
            logs = await fetch_venue_scrape_logs(session, venue.id, now=now)

            # evaluate_staleness=True literally, NOT settings.ENABLE_SCHEDULER.
            # This is not a shortcut standing in for the setting -- it's correct
            # by construction: configure_scheduler() (below) only ever runs under
            # `if settings.ENABLE_SCHEDULER:` in main.py, so this job existing at
            # all already implies the scheduler is on. Reading the setting here
            # would read as a false symmetry with the endpoint, which (unlike
            # this job) can run with the scheduler off.
            current = evaluate_venue_health(venue, logs, now=now, evaluate_staleness=True)
            previous = evaluate_venue_health(venue, logs, now=previous_now, evaluate_staleness=True)

            is_broken_now = current.status in ("warning", "critical")
            was_broken = previous.status in ("warning", "critical")
            if is_broken_now and not was_broken:
                broke.append(current)
            elif was_broken and not is_broken_now:
                recovered.append(previous)

    if not broke and not recovered:
        logger.info("Scrape-health digest: no transitions")
        return

    await send_alert(_format_digest(site_name, broke, recovered))


# --- Error/missed-job reporting ---

def _job_listener(event) -> None:
    """Route APScheduler's own job events through the error-capture funnel.

    Two branches, deliberately not the same path: EVENT_JOB_ERROR carries a real
    exception, so it goes to report_error. EVENT_JOB_MISSED does not — verified in
    the installed apscheduler package, executors/base.py builds the missed event as
    ``JobExecutionEvent(EVENT_JOB_MISSED, job.id, jobstore_alias, run_time)``, four
    positional args against a signature whose ``exception``/``traceback`` default to
    None. Passing that None into report_error would call
    ``logger.error(exc_info=None)`` and (via sentry_hook) ``capture_exception(None)``,
    and the latter falls back to ``sys.exc_info()`` — misattributing an unrelated
    in-flight exception to a missed job, or reporting nothing. A missed job is a
    scheduling problem (the job didn't run at all), not a crash, so it takes its own
    WARNING path carrying the job id and scheduled time instead.
    """
    if event.code == EVENT_JOB_ERROR:
        report_error(event.exception, where="scheduler.job_error", context={"job_id": event.job_id})
    elif event.code == EVENT_JOB_MISSED:
        logger.warning(
            "Job %s missed its scheduled run at %s", event.job_id, event.scheduled_run_time
        )


# --- Scheduler configuration ---

def configure_scheduler():
    """Add all scheduled jobs.

    Cron hours are wall-clock in the region's market timezone (site.timezone),
    not a fixed literal — Triangle's pack pins "America/New_York", the canonical
    IANA id "US/Eastern" used to hardcode (same zone; the alias is converged to
    its canonical form, behavior-identical — region-pack epic decision 10).

    The hour values themselves come from app.cadence (issue #86 part 1) rather than
    a literal here, since the scrape-health evaluator needs the identical table to
    derive its staleness threshold — a second hardcoded copy could drift and produce
    false staleness alarms.
    """
    # remove_listener is silent when absent, so this pair is idempotent like every
    # add_job beside it (replace_existing=True, "safe to call multiple times, e.g.
    # on hot reload"). add_listener itself just appends with no dedupe, so without
    # this a naive call would stack one listener per configure_scheduler() call —
    # and per test that calls it — making "exactly one report_error" assertions
    # order- and xdist-shard-dependent.
    scheduler.remove_listener(_job_listener)
    scheduler.add_listener(_job_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    tz = load_site_config().site.timezone

    # Ticketmaster: 6 AM + 6 PM local
    scheduler.add_job(
        scrape_ticketmaster_job,
        CronTrigger(hour=cron_hour_string("ticketmaster"), timezone=tz),
        id="scrape_ticketmaster",
        replace_existing=True,  # safe to call multiple times (e.g., on hot reload)
    )

    # Indie venues: 6 AM + 12 PM + 6 PM local
    scheduler.add_job(
        scrape_indie_job,
        CronTrigger(hour=cron_hour_string("indie"), timezone=tz),
        id="scrape_indie",
        replace_existing=True,
    )

    # Past event cleanup: 3 AM local
    scheduler.add_job(
        cleanup_past_events_job,
        CronTrigger(hour=3, timezone=tz),
        id="cleanup_past_events",
        replace_existing=True,
    )

    # Scrape-health digest: 7 AM local, after the morning (6 AM) scrape wave so
    # its results are already in ScrapeLog by the time this evaluates (issue #86
    # part 2).
    scheduler.add_job(
        scrape_health_digest_job,
        CronTrigger(hour=7, timezone=tz),
        id="scrape_health_digest",
        replace_existing=True,
    )
