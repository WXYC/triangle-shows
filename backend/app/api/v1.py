"""
Version 1 of the surface-neutral Triangle Shows API.

Role: The canonical, client-agnostic contract — plain event and venue resources with no
presentation baked in (unlike the deprecated /api/events/fullcalendar feed, which returns
FullCalendar-library-shaped objects with server-formatted price/time strings). The web
calendar builds its own presentation from these resources, and the same endpoints are what
a non-web client (e.g. an iOS app via the WXYC Backend-Service) would consume. The
unversioned /api/events, /api/venues, and /api/health routers remain as deprecated
aliases; v1 delegates to the same underlying implementations so the surfaces cannot drift.

Requires: async PostgreSQL session (app.database), EventStatus enum (app.models), response
schemas (app.schemas), shared route helpers (app.api.common), the shared events query
service (app.services.events_query), and the existing health/venues handlers it delegates to.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import event_to_response, get_event_or_404, split_csv
from app.api.health import health_check
from app.api.venues import list_venues as unversioned_list_venues
from app.database import get_session
from app.models import EventStatus
from app.schemas import EventResponse, HealthResponse, VenueResponse
from app.services.events_query import query_events

# --- Router ---

router = APIRouter(prefix="/api/v1", tags=["v1"])


# --- Endpoints ---

@router.get(
    "/events",
    response_model=list[EventResponse],
    summary="List de-duplicated events for a date window",
)
async def list_events(
    start: Optional[date] = Query(None, description="ISO date, inclusive lower bound. Defaults to today; pass an explicit value to query history."),
    end: Optional[date] = Query(None, description="ISO date, inclusive upper bound"),
    city: Optional[str] = Query(None, description="Comma-separated city names"),
    size: Optional[str] = Query(None, description="Comma-separated size categories"),
    venue: Optional[str] = Query(None, description="Comma-separated venue slugs"),
    search: Optional[str] = Query(None, description="Literal match against event name or artist"),
    genre: Optional[str] = Query(None, description="Literal match against genre"),
    status: Optional[EventStatus] = Query(None, description="Ticket/availability status"),
    dedup: bool = Query(True, description="Collapse cross-venue duplicate listings; pass false for every stored row"),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    """All events matching the filters, cross-venue de-duplicated and ordered by date.

    Returns the full matching set (no pagination) — the calendar loads a whole window and
    filters client-side. When `start` is omitted it defaults to today, so an unbounded
    request returns upcoming events rather than the entire history; malformed dates are
    rejected with a 422. De-duplication is relative to the filtered set (a venue filter
    shows that venue's own record of a show); pass `dedup=false` to see every stored row.
    """
    if start is None:
        start = date.today()
    events = await query_events(
        session,
        start=start,
        end=end,
        cities=split_csv(city),
        sizes=split_csv(size),
        venue_slugs=split_csv(venue),
        search=search,
        genre=genre,
        status=status.value if status else None,
        dedup=dedup,
    )
    return [event_to_response(e) for e in events]


@router.get("/events/{event_id}", response_model=EventResponse, summary="Get a single event by id")
async def get_event(event_id: int, session: AsyncSession = Depends(get_session)) -> EventResponse:
    return event_to_response(await get_event_or_404(session, event_id))


@router.get("/venues", response_model=list[VenueResponse], summary="List all venues")
async def list_venues(session: AsyncSession = Depends(get_session)) -> list[VenueResponse]:
    # Delegates to the unversioned handler (ordered by city then name) so the two
    # surfaces stay identical until the alias is removed.
    return await unversioned_list_venues(session)


@router.get("/health", response_model=HealthResponse, summary="Service status and data freshness")
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    # Delegates to the unversioned handler so monitors on either path see the same report.
    return await health_check(session)
