"""
APScheduler job definitions for periodic scraping and data maintenance.

Role: Started during FastAPI app startup (main.py) when ENABLE_SCHEDULER=true.
      Runs scrape jobs on a fixed cron schedule as an alternative to Cloud Scheduler
      HTTP triggers — both ultimately call the same ScrapeManager logic.
Requires: ENABLE_SCHEDULER env var (via config.py), app.scrapers.manager.ScrapeManager,
          app.database.async_session, and a running async event loop (provided by FastAPI).
"""

# --- Imports ---
import logging
from datetime import datetime, timedelta

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete

from app.cadence import cron_hour_string
from app.database import async_session
from app.models import Event
from app.observability import report_error
from app.scrapers.manager import ScrapeManager
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
