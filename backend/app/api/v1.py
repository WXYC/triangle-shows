"""
Version 1 of the surface-neutral Triangle Shows API.

Role: The canonical, client-agnostic contract — plain event and venue resources with no
presentation baked in: no titles, per-venue colors, or server-formatted price/time
strings. The web calendar builds the FullCalendar shape itself from these resources
(frontend/js/fullcalendar-adapter.js), and the same endpoints are what a non-web client
(e.g. an iOS app via the WXYC Backend-Service) would consume. The unversioned /api/events,
/api/venues, and /api/health routers remain as deprecated aliases: venues, health, and the
event-detail route register the same shared handlers (app.api.common), so those surfaces
cannot drift, while the events list route keeps an intentionally different shape
(EventListResponse wrapper, lenient dates) on top of the same query service. Deleting a
deprecated module cannot break v1 — nothing here imports from them.

Requires: async PostgreSQL session (app.database), EventStatus enum (app.models), response
schemas (app.schemas), shared route helpers/handlers (app.api.common), the shared events
query service (app.services.events_query).
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import (
    event_to_response,
    get_event,
    health_check,
    list_venues,
    split_csv,
    today_in_triangle,
)
from app.database import get_session
from app.models import EventStatus
from app.schemas import EventResponse, HealthResponse, VenueResponse
from app.services.events_query import query_events

# --- Router ---

router = APIRouter(prefix="/api/v1", tags=["v1"])

# "Chapel Hill-Carrboro" is a display grouping, not a municipality — venues.city holds
# real towns. The v1 city param keeps accepting the grouping as an alias for both
# municipalities so pre-existing links keep working. Expansion happens per CSV token
# and only at this surface; stored rows never carry the grouping label.
CITY_ALIASES = {"Chapel Hill-Carrboro": ("Chapel Hill", "Carrboro")}


def _expand_city_aliases(cities: Optional[list[str]]) -> Optional[list[str]]:
    if cities is None:
        return None
    return [c for token in cities for c in CITY_ALIASES.get(token, (token,))]


# --- Endpoints ---

@router.get(
    "/events",
    response_model=list[EventResponse],
    summary="List de-duplicated events for a date window",
)
async def list_events(
    start: Optional[date] = Query(None, description="ISO date (YYYY-MM-DD), inclusive lower bound. Defaults to today (America/New_York) when end is also omitted; pass an explicit value to query history."),
    end: Optional[date] = Query(None, description="ISO date (YYYY-MM-DD), inclusive upper bound"),
    city: Optional[str] = Query(None, description="Comma-separated city names"),
    size: Optional[str] = Query(None, description="Comma-separated size categories"),
    venue: Optional[str] = Query(None, description="Comma-separated venue slugs"),
    search: Optional[str] = Query(None, description="Case-insensitive substring match against event name or artist; LIKE wildcards in the input are matched literally"),
    genre: Optional[str] = Query(None, description="Case-insensitive substring match against genre"),
    status: Optional[EventStatus] = Query(None, description="Ticket/availability status"),
    dedup: bool = Query(True, description="Collapse cross-venue duplicate listings; pass false for every stored row"),
    include_removed: bool = Query(False, description="Include soft-removed events (removed_at set: the venue no longer advertises them). Delisting is an observation — it requires misses on two distinct Eastern calendar days (as little as ~12 hours apart under the scheduled scrape cadence) and has a day-of blind spot; consumers decide what it means, and status is never inferred from it. Mirror-style consumers should pass dedup=false to see every tombstoned row AND an explicit back-dated start (e.g. 8 days ago): the default start=today window hides a tombstone stamped on the event's own show date, and rows are hard-deleted 7 days past their date."),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    """All events matching the filters, cross-venue de-duplicated and ordered by date.

    Returns the full matching set (no pagination) — the calendar loads a whole window and
    filters client-side. When neither bound is given, `start` defaults to today in the
    venues' timezone so a bare request returns upcoming events rather than the entire
    history; an explicit `start` (or an `end` on its own) queries history. Malformed
    dates are rejected with a 422. De-duplication semantics live in
    app.services.events_query.query_events; pass `dedup=false` to see every stored row.
    """
    if start is None and end is None:
        start = today_in_triangle()
    events = await query_events(
        session,
        start=start,
        end=end,
        cities=_expand_city_aliases(split_csv(city)),
        sizes=split_csv(size),
        venue_slugs=split_csv(venue),
        search=search,
        genre=genre,
        status=status.value if status else None,
        dedup=dedup,
        include_removed=include_removed,
    )
    return [event_to_response(e) for e in events]


# The event-detail, venues, and health handlers are shared with the deprecated
# unversioned routers — one implementation registered on both surfaces, so they
# cannot drift.
router.add_api_route(
    "/events/{event_id}",
    get_event,
    methods=["GET"],
    response_model=EventResponse,
    summary="Get a single event by id",
)
router.add_api_route(
    "/venues",
    list_venues,
    methods=["GET"],
    response_model=list[VenueResponse],
    summary="List all venues",
)
router.add_api_route(
    "/health",
    health_check,
    methods=["GET"],
    response_model=HealthResponse,
    summary="Service status and data freshness",
)
