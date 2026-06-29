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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete

from app.database import async_session
from app.models import Event
from app.scrapers.manager import ScrapeManager

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
    """Delete events more than 7 days in the past."""
    logger.info("Cleaning up past events")
    # Keep a 7-day buffer so recently-ended events don't vanish immediately
    cutoff = datetime.utcnow().date() - timedelta(days=7)
    async with async_session() as session:
        result = await session.execute(
            delete(Event).where(Event.date < cutoff)
        )
        await session.commit()
        logger.info(f"Deleted {result.rowcount} past events")


# --- Scheduler configuration ---

def configure_scheduler():
    """Add all scheduled jobs."""
    # Ticketmaster: 6 AM + 6 PM ET
    scheduler.add_job(
        scrape_ticketmaster_job,
        CronTrigger(hour="6,18", timezone="US/Eastern"),
        id="scrape_ticketmaster",
        replace_existing=True,  # safe to call multiple times (e.g., on hot reload)
    )

    # Indie venues: 6 AM + 12 PM + 6 PM ET
    scheduler.add_job(
        scrape_indie_job,
        CronTrigger(hour="6,12,18", timezone="US/Eastern"),
        id="scrape_indie",
        replace_existing=True,
    )

    # Past event cleanup: 3 AM ET
    scheduler.add_job(
        cleanup_past_events_job,
        CronTrigger(hour=3, timezone="US/Eastern"),
        id="cleanup_past_events",
        replace_existing=True,
    )
