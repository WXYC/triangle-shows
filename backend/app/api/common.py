"""
Shared route-level helpers and handlers used by both the canonical /api/v1 surface
and the deprecated unversioned routers.

Role: Query-parameter parsing, ORM->schema response mapping, the fetch-or-404 lookup,
and the health/venues handler implementations that are registered on both the v1 and
unversioned routers. Lives in a neutral module so the canonical /api/v1 surface never
depends on the deprecated routers (which are slated for removal once the web client
cuts over) — the deprecated modules import from here, never the reverse.
Requires: async PostgreSQL session (app.database), ORM models (app.models), response
schemas (app.schemas).
"""
import os
import zoneinfo
from datetime import date, datetime
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_session
from app.models import Event, ScrapeLog, Venue
from app.schemas import EventResponse, HealthResponse

# All venues are in the Research Triangle, so "today" for date windows means the
# Triangle's calendar date — not the server's, which runs in UTC in production and
# rolls over at 8 PM Eastern.
TRIANGLE_TZ = zoneinfo.ZoneInfo("America/New_York")


def today_in_triangle() -> date:
    """Current calendar date in the venues' market timezone (America/New_York)."""
    return datetime.now(TRIANGLE_TZ).date()


# --- Query-parameter helpers ---

def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO date, tolerating a full datetime string; None if absent/invalid.

    Lenient by design for the deprecated unversioned endpoints, which historically
    treated invalid input as "no filter" so a malformed calendar request still
    returns events. The /api/v1 endpoints instead declare typed `date` parameters
    and reject malformed input with a 422.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def split_csv(value: Optional[str]) -> Optional[list[str]]:
    """Split a comma-separated query value into a trimmed list.

    Returns None when the parameter is absent/empty (no filter). Empty segments are
    dropped, but a value that is *present* yet contains no usable segments (e.g.
    ",,") returns [] — a filter that matches nothing — preserving the historical
    behavior of the unversioned endpoints.
    """
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


# --- Response mapping ---

def event_to_response(event: Event) -> EventResponse:
    """Map an ORM Event (with venue eagerly loaded) to the EventResponse schema.

    Scalar fields flow through from_attributes validation (which also stamps the
    UTC offset on updated_at); only the denormalized venue_* fields need wiring.
    """
    response = EventResponse.model_validate(event)
    if event.venue:
        response.venue_name = event.venue.name
        response.venue_slug = event.venue.slug
        response.venue_city = event.venue.city
        response.venue_color = event.venue.color
    return response


# --- Lookups ---

async def get_event_or_404(session: AsyncSession, event_id: int) -> Event:
    """Fetch one Event by primary key (venue eagerly loaded) or raise 404."""
    event = await session.get(Event, event_id, options=[joinedload(Event.venue)])
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


# --- Shared handlers (registered on both the v1 and unversioned routers) ---

async def list_venues(session: AsyncSession = Depends(get_session)) -> list[Venue]:
    """All venues, ordered by city then name so clients can group by market."""
    result = await session.execute(select(Venue).order_by(Venue.city, Venue.name))
    # ORM rows; the routes' response_model performs the (single) validation pass.
    return list(result.scalars().all())


async def health_check(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    """Service status: event/venue counts, last successful scrape, build version."""
    event_count = (await session.execute(select(func.count(Event.id)))).scalar()
    venue_count = (await session.execute(select(func.count(Venue.id)))).scalar()
    last_scrape = (
        await session.execute(
            select(ScrapeLog.finished_at)
            .where(ScrapeLog.status == "success")
            .order_by(ScrapeLog.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return HealthResponse(
        status="ok",
        event_count=event_count,
        venue_count=venue_count,
        last_scrape=last_scrape,
        version=os.environ.get("GIT_COMMIT", "unknown"),  # set by Cloud Build at image build time
    )
