"""Event API endpoints."""
import re
from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_session
from app.models import Event, Venue
from app.schemas import EventResponse, FullCalendarEvent, EventListResponse

router = APIRouter(prefix="/api/events", tags=["events"])


def _event_to_response(event: Event) -> EventResponse:
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
        venue_name=event.venue.name if event.venue else None,
        venue_slug=event.venue.slug if event.venue else None,
        venue_city=event.venue.city if event.venue else None,
        venue_color=event.venue.color if event.venue else None,
    )


@router.get("/fullcalendar")
async def get_fullcalendar_events(
    start: Optional[str] = Query(None, description="ISO date start"),
    end: Optional[str] = Query(None, description="ISO date end"),
    city: Optional[str] = Query(None),
    size: Optional[str] = Query(None),
    venue: Optional[str] = Query(None, description="Comma-separated venue slugs"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """FullCalendar JSON feed endpoint."""
    needs_venue_join = bool(city or size or venue)

    conditions = []

    if start:
        try:
            start_date = date.fromisoformat(start[:10])
            conditions.append(Event.date >= start_date)
        except ValueError:
            pass

    if end:
        try:
            end_date = date.fromisoformat(end[:10])
            conditions.append(Event.date <= end_date)
        except ValueError:
            pass

    if city:
        conditions.append(Venue.city.in_([c.strip() for c in city.split(",")]))

    if size:
        conditions.append(Venue.size_category.in_([s.strip() for s in size.split(",")]))

    if venue:
        conditions.append(Venue.slug.in_([s.strip() for s in venue.split(",")]))

    query = select(Event).options(joinedload(Event.venue))
    if needs_venue_join:
        query = query.join(Event.venue)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(Event.date)

    result = await session.execute(query)
    events = result.unique().scalars().all()

    # Cross-venue dedup: if the same artist performs on the same date at two
    # different venues (e.g. listed on both a venue's own site and Ticketmaster),
    # keep only the entry with the most complete metadata.
    _dedup_best: dict[tuple, Event] = {}
    _dedup_score: dict[tuple, int] = {}
    for event in events:
        label = event.artist or event.name
        norm = re.sub(r"[^a-z0-9]", "", label.lower())
        key = (event.date, norm)
        score = bool(event.image_url) + bool(event.ticket_url) + (event.price_min is not None)
        if key not in _dedup_best:
            _dedup_best[key] = event
            _dedup_score[key] = score
        elif event.venue_id != _dedup_best[key].venue_id and score > _dedup_score[key]:
            _dedup_best[key] = event
            _dedup_score[key] = score
    kept = {ev.id for ev in _dedup_best.values()}
    events = [e for e in events if e.id in kept]

    # DPAC: collapse to one chip per date. TM lists box seats, VIP packages, and
    # matinee/evening variants as separate event IDs that survive the name-based
    # dedup above. A single chip per day is cleaner on the calendar.
    # Prefer on_sale events over sold_out ones so a Boxes (offsale) variant
    # doesn't win when a regular on_sale listing exists for the same date.
    dpac_best: dict = {}
    for event in events:
        if event.venue and event.venue.slug == "dpac":
            d = event.date
            if d not in dpac_best:
                dpac_best[d] = event
            elif dpac_best[d].status != "on_sale" and event.status == "on_sale":
                dpac_best[d] = event
    dpac_ids = {e.id for e in dpac_best.values()}
    collapsed = []
    for event in events:
        if event.venue and event.venue.slug == "dpac":
            if event.id in dpac_ids:
                collapsed.append(event)
        else:
            collapsed.append(event)
    events = collapsed

    fc_events = []
    for event in events:
        venue_obj = event.venue
        color = venue_obj.color if venue_obj else "#6366f1"

        # Always use date-only so FullCalendar renders all events as
        # all-day blocks in month view (consistent colored boxes).
        # The actual show time is still available in extendedProps.show_time.
        start_str = event.date.isoformat()

        # Format price
        price_str = None
        if event.price_min is not None:
            if event.price_min == 0 and (event.price_max is None or event.price_max == 0):
                price_str = "Free"
            elif event.price_max and event.price_max != event.price_min:
                price_str = f"${event.price_min:.0f}-${event.price_max:.0f}"
            else:
                price_str = f"${event.price_min:.0f}"

        fc_events.append({
            "id": event.id,
            "title": event.artist or event.name,
            "start": start_str,
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
                "doors_time": event.doors_time.strftime("%I:%M %p").lstrip("0") if event.doors_time else None,
                "show_time": event.show_time.strftime("%I:%M %p").lstrip("0") if event.show_time else None,
                "ticket_url": event.ticket_url,
                "price": price_str,
                "price_min": event.price_min,
                "price_max": event.price_max,
                "image_url": event.image_url,
                "genre": event.genre,
                "subgenre": event.subgenre,
                "status": event.status,
                "age_restriction": event.age_restriction,
                "description": event.description,
            },
        })

    return fc_events


@router.get("/{event_id}")
async def get_event(
    event_id: int,
    session: AsyncSession = Depends(get_session),
) -> EventResponse:
    """Get a single event by ID."""
    result = await session.execute(
        select(Event).options(joinedload(Event.venue)).where(Event.id == event_id)
    )
    event = result.unique().scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return _event_to_response(event)


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
    """List events with filters and pagination."""
    query = select(Event).options(joinedload(Event.venue)).order_by(Event.date)
    count_query = select(func.count(Event.id))

    conditions = []

    if start:
        try:
            conditions.append(Event.date >= date.fromisoformat(start[:10]))
        except ValueError:
            pass
    if end:
        try:
            conditions.append(Event.date <= date.fromisoformat(end[:10]))
        except ValueError:
            pass
    if search:
        search_term = f"%{search}%"
        conditions.append(
            Event.name.ilike(search_term) | Event.artist.ilike(search_term)
        )
    if genre:
        conditions.append(Event.genre.ilike(f"%{genre}%"))
    if status:
        conditions.append(Event.status == status)

    if conditions:
        query = query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    total_result = await session.execute(count_query)
    total = total_result.scalar()

    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await session.execute(query)
    events = result.unique().scalars().all()

    return EventListResponse(
        events=[_event_to_response(e) for e in events],
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page if total else 0,
    )
