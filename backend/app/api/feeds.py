"""iCal subscription feed — live, filterable by venue."""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from icalendar import Calendar, Event as ICalEvent, vText
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_session
from app.models import Event, Venue

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("/events.ics", response_class=Response)
async def get_ical_feed(
    venue: Optional[str] = Query(None, description="Comma-separated venue slugs. Omit for all venues."),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Live iCal subscription feed. Add to Apple Calendar, Google Calendar, or Outlook once;
    new shows appear automatically as the scraper finds them."""

    today = date.today()
    conditions = [Event.date >= today]

    needs_join = bool(venue)
    if venue:
        slugs = [s.strip() for s in venue.split(",") if s.strip()]
        conditions.append(Venue.slug.in_(slugs))

    query = select(Event).options(joinedload(Event.venue))
    if needs_join:
        query = query.join(Event.venue)
    query = query.where(and_(*conditions)).order_by(Event.date)

    result = await session.execute(query)
    events = result.unique().scalars().all()

    cal = Calendar()
    cal.add("prodid", "-//triangle-shows.org//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", vText("Triangle Shows"))
    cal.add("x-wr-caldesc", vText("Live music across the Triangle — triangle-shows.org"))
    cal.add("x-wr-timezone", vText("America/New_York"))
    # Suggest clients refresh every 6 hours
    cal.add("refresh-interval;value=duration", "PT6H")
    cal.add("x-published-ttl", "PT6H")

    now = datetime.now(timezone.utc)

    for event in events:
        venue_obj = event.venue
        iev = ICalEvent()

        iev.add("uid",     vText(f"{event.id}@triangle-shows.org"))
        iev.add("dtstamp", now)

        # Summary: prefer artist name, fall back to event name
        summary = event.artist or event.name
        iev.add("summary", vText(summary))

        # All-day or timed event
        if event.show_time:
            from datetime import datetime as dt
            import zoneinfo
            tz = zoneinfo.ZoneInfo("America/New_York")
            start = dt.combine(event.date, event.show_time, tzinfo=tz)
            iev.add("dtstart", start)
            iev.add("dtend",   start + timedelta(hours=3))
        else:
            iev.add("dtstart", event.date)
            iev.add("dtend",   event.date + timedelta(days=1))

        # Location
        if venue_obj:
            iev.add("location", vText(f"{venue_obj.name}, {venue_obj.city}, NC"))

        # Description — pack in the useful bits
        desc_parts = []
        if event.name and event.name != summary:
            desc_parts.append(event.name)
        if event.support_artists:
            desc_parts.append(f"w/ {event.support_artists}")
        if event.doors_time:
            desc_parts.append(f"Doors: {event.doors_time.strftime('%-I:%M %p')}")
        if event.show_time:
            desc_parts.append(f"Show: {event.show_time.strftime('%-I:%M %p')}")
        if event.price_min is not None:
            if event.price_min == 0:
                desc_parts.append("Free")
            elif event.price_max and event.price_max != event.price_min:
                desc_parts.append(f"${event.price_min:.0f}–${event.price_max:.0f}")
            else:
                desc_parts.append(f"${event.price_min:.0f}")
        if event.age_restriction:
            desc_parts.append(event.age_restriction)
        if event.ticket_url:
            desc_parts.append(f"\n{event.ticket_url}")
        if desc_parts:
            iev.add("description", vText("\n".join(desc_parts)))

        # URL
        if event.ticket_url:
            iev.add("url", event.ticket_url)

        cal.add_component(iev)

    ical_bytes = cal.to_ical()
    return Response(
        content=ical_bytes,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="triangle-shows.ics"',
            "Cache-Control": "public, max-age=3600",
        },
    )
