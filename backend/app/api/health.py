"""
GET /api/health — returns current status, event/venue counts, last scrape time, and git SHA.

Role: Lightweight liveness/readiness probe consumed by Cloud Run, uptime monitors, and
      the /deploy skill to confirm a new revision is live. Also exposes scrape freshness
      so operators can tell at a glance whether data is up to date.
Requires: async DB session (app.database), Event/Venue/ScrapeLog models, HealthResponse
          schema, and the GIT_COMMIT env var (injected at build time by Cloud Build).
"""
# --- Imports ---
import os
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Event, Venue, ScrapeLog
from app.schemas import HealthResponse

# --- Router ---
router = APIRouter(prefix="/api/health", tags=["health"], deprecated=True)  # superseded by /api/v1/health (same handler)


# --- Endpoint ---

@router.get("", response_model=HealthResponse)
async def health_check(session: AsyncSession = Depends(get_session)):
    """Health check with event count and last scrape info."""
    event_count = (await session.execute(select(func.count(Event.id)))).scalar()
    venue_count = (await session.execute(select(func.count(Venue.id)))).scalar()

    # Pull the most recent successful scrape timestamp for freshness reporting
    last_scrape_result = await session.execute(
        select(ScrapeLog.finished_at)
        .where(ScrapeLog.status == "success")
        .order_by(ScrapeLog.finished_at.desc())
        .limit(1)
    )
    last_scrape = last_scrape_result.scalar_one_or_none()

    return HealthResponse(
        status="ok",
        event_count=event_count,
        venue_count=venue_count,
        last_scrape=last_scrape,
        version=os.environ.get("GIT_COMMIT", "unknown"),  # set by Cloud Build at image build time
    )
