"""
Event API endpoints for the Triangle Shows calendar (deprecated aliases).

Role: Serves GET /api/events/{id} and GET /api/events (paginated list) as deprecated
aliases of the surface-neutral /api/v1 endpoints (see app.api.v1). The fetch/filter/
de-duplication logic lives in app.services.events_query and the parameter/response
helpers in app.api.common, so every surface shares them; these handlers only shape the
response. The FullCalendar-shaped feed that once lived here (GET /api/events/fullcalendar)
has been removed — the web client now builds the FullCalendar shape itself from
/api/v1/events (see frontend/js/fullcalendar-adapter.js).
Requires: async PostgreSQL session (app.database), response schemas (app.schemas),
shared helpers (app.api.common), the shared query service (app.services.events_query).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import event_to_response, get_event, parse_date
from app.database import get_session
from app.schemas import EventResponse, EventListResponse
from app.services.events_query import query_events

# --- Router setup ---

router = APIRouter(prefix="/api/events", tags=["events"], deprecated=True)  # superseded by /api/v1/events


# --- Endpoints ---

# Single-event detail — the same shared handler serves /api/v1/events/{event_id}.
router.add_api_route(
    "/{event_id}",
    get_event,
    methods=["GET"],
    response_model=EventResponse,
    summary="Get a single event by id (deprecated alias of /api/v1/events/{event_id})",
)


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

    Shape note: `support_artists` serializes as a JSON array of strings here too, not the
    historical comma-joined string. This deprecated surface flips deliberately alongside
    `/api/v1/events` — a uniform shape with zero external consumers to protect (see
    backend/README.md, "API contracts").

    Uses the shared query service, so results are cross-venue de-duplicated (matching
    the calendar feed) and `search`/`genre` match substrings literally (LIKE wildcards
    in the input are escaped — an intentional v1.1 change from the historical raw-pattern
    behavior). Because de-duplication happens in Python, the full matching set is fetched
    per request, pagination is applied to the de-duplicated set, and `total` reflects the
    de-duplicated count. That is an accepted tradeoff at this dataset's size (a few
    thousand rows); if the events table grows past ~50k rows, push the de-duplication
    into SQL and restore COUNT + LIMIT/OFFSET.
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
