"""APScheduler job definitions."""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete

from app.database import async_session
from app.models import Event
from app.scrapers.manager import ScrapeManager

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


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
    cutoff = datetime.utcnow().date() - timedelta(days=7)
    async with async_session() as session:
        result = await session.execute(
            delete(Event).where(Event.date < cutoff)
        )
        await session.commit()
        logger.info(f"Deleted {result.rowcount} past events")


def configure_scheduler():
    """Add all scheduled jobs."""
    # Ticketmaster: 6 AM + 6 PM ET
    scheduler.add_job(
        scrape_ticketmaster_job,
        CronTrigger(hour="6,18", timezone="US/Eastern"),
        id="scrape_ticketmaster",
        replace_existing=True,
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
