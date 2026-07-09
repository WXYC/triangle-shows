"""
Shared route-level helpers used by both the canonical /api/v1 surface and the
deprecated unversioned routers.

Role: Query-parameter parsing, ORM->schema response mapping, and the fetch-or-404
lookup that every event-serving router needs. Lives in a neutral module so the
canonical /api/v1 surface never depends on the deprecated routers (which are slated
for removal once the web client cuts over).
Requires: Event ORM model (app.models), response schemas (app.schemas), async
PostgreSQL session (passed in by the caller).
"""
from datetime import date, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Event
from app.schemas import EventResponse


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

    Empty segments are dropped; returns None if nothing remains (treated by the
    query service as "no filter").
    """
    if not value:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return parts or None


# --- Response mapping ---

def event_to_response(event: Event) -> EventResponse:
    """Map an ORM Event (with venue eagerly loaded) to the EventResponse schema."""
    return EventResponse(
        id=event.id,
        venue_id=event.venue_id,
        name=event.name,
        artist=event.artist,
        support_artists=event.support_artists,
        date=event.date,
        doors_time=event.doors_time,
        show_time=event.show_time,
        ticket_url=event.ticket_url,
        price_min=event.price_min,
        price_max=event.price_max,
        image_url=event.image_url,
        genre=event.genre,
        subgenre=event.subgenre,
        status=event.status,
        age_restriction=event.age_restriction,
        description=event.description,
        source=event.source,
        # Stored as naive UTC (datetime.utcnow); mark it explicitly so clients get
        # an unambiguous offset instead of a bare local-looking timestamp.
        updated_at=event.updated_at.replace(tzinfo=timezone.utc) if event.updated_at else None,
        venue_name=event.venue.name if event.venue else None,
        venue_slug=event.venue.slug if event.venue else None,
        venue_city=event.venue.city if event.venue else None,
        venue_color=event.venue.color if event.venue else None,
    )


# --- Lookups ---

async def get_event_or_404(session: AsyncSession, event_id: int) -> Event:
    """Fetch one Event (venue eagerly loaded) or raise 404."""
    result = await session.execute(
        select(Event).options(joinedload(Event.venue)).where(Event.id == event_id)
    )
    event = result.unique().scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
