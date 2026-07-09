"""
Event API endpoints for the Triangle Shows calendar.

Role: Serves GET /api/events/fullcalendar (the current web feed), GET /api/events/{id},
and GET /api/events (paginated list). The fetch/filter/de-duplication logic lives in
app.services.events_query so every surface shares it; these handlers only shape the
response. The FullCalendar feed is deprecated in favor of the surface-neutral
/api/v1/events feed (see app.api.v1) and will be removed once the web client builds
the FullCalendar shape itself.
Requires: async PostgreSQL session (app.database), Event ORM model (app.models),
response schemas (app.schemas), the shared query service (app.services.events_query).
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_session
from app.models import Event
from app.schemas import EventResponse, EventListResponse
from app.services.events_query import query_events

# --- Router setup ---

router = APIRouter(prefix="/api/events", tags=["events"], deprecated=True)  # superseded by /api/v1/events


# --- Query-parameter helpers ---

def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO date, tolerating a full datetime string; None if absent/invalid.

    Invalid input is treated as "no filter" (matching the previous behavior) rather
    than raising, so a malformed calendar request still returns events.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _split_csv(value: Optional[str]) -> Optional[list[str]]:
    """Split a comma-separated query value into a trimmed list; None if absent."""
    if not value:
        return None
    return [part.strip() for part in value.split(",")]


# --- Response mapping ---

def _event_to_response(event: Event) -> EventResponse:
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
        updated_at=event.updated_at,
        venue_name=event.venue.name if event.venue else None,
        venue_slug=event.venue.slug if event.venue else None,
        venue_city=event.venue.city if event.venue else None,
        venue_color=event.venue.color if event.venue else None,
    )


def _format_price(price_min: Optional[float], price_max: Optional[float]) -> Optional[str]:
    """Human-readable price string for the FullCalendar payload (server-side, legacy)."""
    if price_min is None:
        return None
    if price_min == 0 and (price_max is None or price_max == 0):
        return "Free"
    if price_max and price_max != price_min:
        return f"${price_min:.0f}-${price_max:.0f}"
    return f"${price_min:.0f}"


def _to_fullcalendar(event: Event) -> dict:
    """Shape an Event as the FullCalendar v6 event object the current web UI expects."""
    venue_obj = event.venue
    color = venue_obj.color if venue_obj else "#6366f1"
    return {
        "id": event.id,
        # All events render as all-day blocks in month view; the real time is in
        # extendedProps.show_time.
        "title": event.artist or event.name,
        "start": event.date.isoformat(),
        "allDay": True,
        "backgroundColor": color,
        "borderColor": color,
        "textColor": "#ffffff",
        "extendedProps": {
            "event_id": event.id,
            "name": event.name,
            "artist": event.artist,
            "support_artists": event.support_artists,
            "venue_name": venue_obj.name if venue_obj else None,
            "venue_slug": venue_obj.slug if venue_obj else None,
            "venue_city": venue_obj.city if venue_obj else None,
            "venue_color": color,
            "date": event.date.isoformat(),
            # Strip the leading zero from the hour (e.g. "9:00 PM" not "09:00 PM").
            "doors_time": event.doors_time.strftime("%I:%M %p").lstrip("0") if event.doors_time else None,
            "show_time": event.show_time.strftime("%I:%M %p").lstrip("0") if event.show_time else None,
            "ticket_url": event.ticket_url,
            "price": _format_price(event.price_min, event.price_max),
            "price_min": event.price_min,
            "price_max": event.price_max,
            "image_url": event.image_url,
            "genre": event.genre,
            "subgenre": event.subgenre,
            "status": event.status,
            "age_restriction": event.age_restriction,
            "description": event.description,
        },
    }


# --- Endpoints ---

@router.get("/fullcalendar", deprecated=True)
async def get_fullcalendar_events(
    start: Optional[str] = Query(None, description="ISO date start"),
    end: Optional[str] = Query(None, description="ISO date end"),
    city: Optional[str] = Query(None, description="Comma-separated city names"),
    size: Optional[str] = Query(None, description="Comma-separated size categories"),
    venue: Optional[str] = Query(None, description="Comma-separated venue slugs"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """FullCalendar JSON feed (deprecated).

    Deprecated in favor of the surface-neutral GET /api/v1/events; the web client will
    build the FullCalendar shape itself. Kept working until that cut-over lands.
    """
    events = await query_events(
        session,
        start=_parse_date(start),
        end=_parse_date(end),
        cities=_split_csv(city),
        sizes=_split_csv(size),
        venue_slugs=_split_csv(venue),
    )
    return [_to_fullcalendar(event) for event in events]


async def get_event_or_404(session: AsyncSession, event_id: int) -> Event:
    """Fetch one Event (venue eagerly loaded) or raise 404. Shared with the /api/v1 handler."""
    result = await session.execute(
        select(Event).options(joinedload(Event.venue)).where(Event.id == event_id)
    )
    event = result.unique().scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/{event_id}")
async def get_event(
    event_id: int,
    session: AsyncSession = Depends(get_session),
) -> EventResponse:
    """Get a single event by ID."""
    return _event_to_response(await get_event_or_404(session, event_id))


@router.get("")
async def list_events(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> EventListResponse:
    """List events with filters and pagination.

    Uses the shared query service, so results are cross-venue de-duplicated (matching
    the calendar feed). Because de-duplication happens in Python, pagination is applied
    to the de-duplicated set and `total` reflects the de-duplicated count.
    """
    events = await query_events(
        session,
        start=_parse_date(start),
        end=_parse_date(end),
        search=search,
        genre=genre,
        status=status,
        dedup=True,
    )
    total = len(events)
    offset = (page - 1) * per_page
    page_events = events[offset:offset + per_page]

    return EventListResponse(
        events=[_event_to_response(e) for e in page_events],
        total=total,
        page=page,
        per_page=per_page,
        # Integer ceiling division to get total page count.
        pages=(total + per_page - 1) // per_page if total else 0,
    )
