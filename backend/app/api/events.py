"""
Event API endpoints for the Triangle Shows calendar.

Role: Serves GET /api/events/fullcalendar (the current web feed), GET /api/events/{id},
and GET /api/events (paginated list). The fetch/filter/de-duplication logic lives in
app.services.events_query and the parameter/response helpers in app.api.common, so
every surface shares them; these handlers only shape the response. The FullCalendar
feed is deprecated in favor of the surface-neutral /api/v1/events feed (see app.api.v1)
and will be removed once the web client builds the FullCalendar shape itself.
Requires: async PostgreSQL session (app.database), Event ORM model (app.models),
response schemas (app.schemas), shared helpers (app.api.common), the shared query
service (app.services.events_query).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import event_to_response, get_event_or_404, parse_date, split_csv
from app.database import get_session
from app.models import Event
from app.schemas import EventResponse, EventListResponse
from app.services.events_query import query_events

# --- Router setup ---

router = APIRouter(prefix="/api/events", tags=["events"], deprecated=True)  # superseded by /api/v1/events


# --- FullCalendar shaping (legacy) ---

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

@router.get("/fullcalendar")
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
    build the FullCalendar shape itself. Kept working until that cut-over lands. Invalid
    date parameters are treated as "no filter" (historical behavior this feed's consumers
    rely on).
    """
    events = await query_events(
        session,
        start=parse_date(start),
        end=parse_date(end),
        cities=split_csv(city),
        sizes=split_csv(size),
        venue_slugs=split_csv(venue),
    )
    return [_to_fullcalendar(event) for event in events]


@router.get("/{event_id}")
async def get_event(
    event_id: int,
    session: AsyncSession = Depends(get_session),
) -> EventResponse:
    """Get a single event by ID."""
    return event_to_response(await get_event_or_404(session, event_id))


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
    the calendar feed). Because de-duplication happens in Python, the full matching set
    is fetched per request, pagination is applied to the de-duplicated set, and `total`
    reflects the de-duplicated count. That is an accepted tradeoff at this dataset's
    size (a few thousand rows); if the events table grows past ~50k rows, push the
    de-duplication into SQL and restore COUNT + LIMIT/OFFSET.
    """
    events = await query_events(
        session,
        start=parse_date(start),
        end=parse_date(end),
        search=search,
        genre=genre,
        status=status,
    )
    total = len(events)
    offset = (page - 1) * per_page
    page_events = events[offset:offset + per_page]

    return EventListResponse(
        events=[event_to_response(e) for e in page_events],
        total=total,
        page=page,
        per_page=per_page,
        # Integer ceiling division to get total page count.
        pages=(total + per_page - 1) // per_page if total else 0,
    )
