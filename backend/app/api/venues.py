"""Venue API endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Venue
from app.schemas import VenueResponse

router = APIRouter(prefix="/api/venues", tags=["venues"])


@router.get("", response_model=list[VenueResponse])
async def list_venues(session: AsyncSession = Depends(get_session)):
    """Get all venues with metadata for the filter UI."""
    result = await session.execute(select(Venue).order_by(Venue.city, Venue.name))
    venues = result.scalars().all()
    return [VenueResponse.model_validate(v) for v in venues]
