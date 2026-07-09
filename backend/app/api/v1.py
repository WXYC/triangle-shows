"""
Version 1 of the surface-neutral Triangle Shows API.

Role: The canonical, client-agnostic contract — plain event and venue resources with no
presentation baked in (unlike the deprecated /api/events/fullcalendar feed, which returns
FullCalendar-library-shaped objects with server-formatted price/time strings). The web
calendar builds its own presentation from these resources, and the same endpoints are what
a non-web client (e.g. an iOS app via the WXYC Backend-Service) would consume. The
unversioned /api/events and /api/venues routers remain as deprecated aliases.

Requires: async PostgreSQL session (app.database), ORM models (app.models), response
schemas (app.schemas), and the shared events query service (app.services.events_query).
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.events import _event_to_response, _parse_date, _split_csv, get_event_or_404
from app.database import get_session
from app.models import Event, ScrapeLog, Venue
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
    start: Optional[str] = Query(None, description="ISO date, inclusive lower bound"),
    end: Optional[str] = Query(None, description="ISO date, inclusive upper bound"),
    city: Optional[str] = Query(None, description="Comma-separated city names"),
    size: Optional[str] = Query(None, description="Comma-separated size categories"),
    venue: Optional[str] = Query(None, description="Comma-separated venue slugs"),
    search: Optional[str] = Query(None, description="Match against event name or artist"),
    genre: Optional[str] = Query(None, description="Match against genre"),
    status: Optional[str] = Query(None, description="Exact ticket/availability status"),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    """All events matching the filters, cross-venue de-duplicated and ordered by date.

    Returns the full matching set (no pagination) — the calendar loads a whole window and
    filters client-side. Bound the result with `start`/`end`.
    """
    events = await query_events(
        session,
        start=_parse_date(start),
        end=_parse_date(end),
        cities=_split_csv(city),
        sizes=_split_csv(size),
        venue_slugs=_split_csv(venue),
        search=search,
        genre=genre,
        status=status,
    )
    return [_event_to_response(e) for e in events]


@router.get("/events/{event_id}", response_model=EventResponse, summary="Get a single event by id")
async def get_event(event_id: int, session: AsyncSession = Depends(get_session)) -> EventResponse:
    return _event_to_response(await get_event_or_404(session, event_id))


@router.get("/venues", response_model=list[VenueResponse], summary="List all venues")
async def list_venues(session: AsyncSession = Depends(get_session)) -> list[VenueResponse]:
    # Ordered by city then name so clients can group by market without a second sort.
    result = await session.execute(select(Venue).order_by(Venue.city, Venue.name))
    return [VenueResponse.model_validate(v) for v in result.scalars().all()]


@router.get("/health", response_model=HealthResponse, summary="Service status and data freshness")
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
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
        version=os.environ.get("GIT_COMMIT", "unknown"),
    )
