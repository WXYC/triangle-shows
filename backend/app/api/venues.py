"""
Exposes the GET /api/venues endpoint, returning all venues for the frontend filter UI.

Role: Called by the frontend on page load to populate the venue filter sidebar;
      not part of the scrape pipeline — purely a read-only query endpoint.
Requires: app.database (async PostgreSQL session), app.models.Venue, app.schemas.VenueResponse.
"""

# --- Imports ---
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Venue
from app.schemas import VenueResponse

# --- Router ---

router = APIRouter(prefix="/api/venues", tags=["venues"])


# --- Endpoints ---

@router.get("", response_model=list[VenueResponse])
async def list_venues(session: AsyncSession = Depends(get_session)):
    """Get all venues with metadata for the filter UI."""
    # Order by city first so the frontend can group venues by market (Raleigh, Durham, Chapel Hill)
    result = await session.execute(select(Venue).order_by(Venue.city, Venue.name))
    venues = result.scalars().all()
    return [VenueResponse.model_validate(v) for v in venues]
