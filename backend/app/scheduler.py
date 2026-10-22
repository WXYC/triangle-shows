"""
APScheduler job definitions for periodic scraping.

Role: Started during FastAPI app startup (main.py) when ENABLE_SCHEDULER=true.
      Runs scrape jobs on a fixed cron schedule as an alternative to Cloud Scheduler
      HTTP triggers — both ultimately call the same ScrapeManager logic. Every job here
      only ever adds or updates events; nothing scheduled deletes them, deliberately
      (see configure_scheduler).
Requires: ENABLE_SCHEDULER env var (via config.py), app.scrapers.manager.ScrapeManager,
          app.database.async_session, and a running async event loop (provided by FastAPI).
"""

# --- Imports ---
#
# No `delete`, no Event model, no date arithmetic: every import here is for scheduling or
# scraping. That is a property worth noticing rather than an accident — this module cannot
# delete a row without someone adding an import first.
import logging

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import async_session
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
        CronTrigger(hour="6,18", timezone=tz),
        id="scrape_ticketmaster",
        replace_existing=True,  # safe to call multiple times (e.g., on hot reload)
    )

    # Indie venues: 6 AM + 12 PM + 6 PM local
    scheduler.add_job(
        scrape_indie_job,
        CronTrigger(hour="6,12,18", timezone=tz),
        id="scrape_indie",
        replace_existing=True,
    )

    # No past-event cleanup job, deliberately. Removed rather than left commented out,
    # because commented-out code gets re-enabled by someone who reads the comment as a
    # to-do rather than a decision.
    #
    # There used to be one deleting every event more than 7 days old, nightly at 3 AM ET.
    # It never ran anywhere — ENABLE_SCHEDULER is false in cloudbuild.yaml, in
    # backend/.env.example, in docs/SELF-HOSTING.md and by default in config.py — which is
    # the only reason the archive still exists: production holds events back to January.
    #
    # Past events are wanted, and three separate parts of the app already assume so:
    #
    #   * the scrape's snapshot diff bounds tombstoning to `today <= date < horizon` and
    #     never deletes, so a scrape cannot remove the archive (WXYC Backend-Service
    #     imports past events with a back-dated start)
    #   * the admin moderation queue and reclassify_all() both work from reclassify_floor(),
    #     which is today minus 30 days — a 7-day cleanup would delete rows the admin UI is
    #     built to display
    #   * the public calendar can be scrolled backwards, and /api/events applies no floor
    #
    # The job also bypassed the two protections that make hand-added events and
    # admin-flagged duplicates safe: it issued a bulk DELETE, so it never reached
    # ScrapeManager.scraper_must_not_delete, and would have silently undone both a week
    # after the fact.
    #
    # There is no operational pressure the other way. Production is at ~2,900 events after
    # a year across 22 venues; the table is not going to become a problem.
