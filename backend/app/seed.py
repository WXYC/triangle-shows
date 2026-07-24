"""
Idempotently inserts or updates every venue in the active region's venue pack into
the database.

Role: Called once at application startup (from main.py) before the scheduler
begins. Runs after init_db() so the schema is guaranteed to exist. Also removes
discontinued venues so stale data doesn't accumulate. The venue roster itself is
declarative config, not a Python literal — see app.site_config.load_venue_config()
and backend/config/regions/<REGION>/venues.toml (region-pack epic, issue #62/#63).
Requires: DATABASE_URL env var (via config.py), app.database, app.models,
app.site_config.
"""

# --- Imports ---
import asyncio
import logging
from sqlalchemy import select
from app.database import async_session, init_db
from app.models import Venue
from app.site_config import load_venue_config

logger = logging.getLogger(__name__)

# --- Venue Definitions ---
# Sourced from the active region's venues.toml (see app.site_config), not a Python
# literal. Each dict maps directly to Venue model columns; scraper_type determines
# which scraper class handles the venue and scraper_config passes venue-specific
# options (URL, filters, account IDs) to it — see venues.toml for the full
# scraper -> venue mapping and per-venue comments.
_venue_config = load_venue_config()
VENUES = [v.to_venue_dict() for v in _venue_config.venue]
REMOVED_SLUGS: list[str] = _venue_config.removed_slugs


# --- Seed Function ---

async def seed_venues():
    """Insert or update all venues from the active region's venue pack."""
    await init_db()
    async with async_session() as session:
        # Remove discontinued venues (cascade deletes their events)
        for slug in REMOVED_SLUGS:
            result = await session.execute(select(Venue).where(Venue.slug == slug))
            venue = result.scalar_one_or_none()
            if venue:
                await session.delete(venue)
                logger.info(f"Deleted discontinued venue: {slug}")
        await session.commit()

        count_new = 0
        count_updated = 0
        for venue_data in VENUES:
            result = await session.execute(
                select(Venue).where(Venue.slug == venue_data["slug"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                # Overwrite all fields so changes to VENUES propagate on next startup
                for key, value in venue_data.items():
                    setattr(existing, key, value)
                count_updated += 1
            else:
                session.add(Venue(**venue_data))
                count_new += 1
        await session.commit()
        logger.info(f"Seed complete: {count_new} new, {count_updated} updated venues")
        print(f"Seed complete: {count_new} new, {count_updated} updated venues")


# --- CLI Entry Point ---
# Allows running `python -m app.seed` directly for manual re-seeding during development
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_venues())
