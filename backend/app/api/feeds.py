"""
Generates the iCal subscription feed served at GET /feeds/events.ics.

Role: Consumed directly by calendar clients (Apple Calendar, Google Calendar, Outlook).
Users subscribe once; the feed stays live and reflects whatever the scraper has loaded
into the database. Optionally filtered to one or more venues via ?venue= slug. Every
successfully served fetch also records a best-effort telemetry row (record_feed_fetch)
since calendar pollers never execute JS, so this is the only server-side measure of
feed reach (issue #88).
Requires: PostgreSQL (via app.database), the shared events query service
(app.services.events_query), shared param helpers (app.api.common), icalendar library.
"""

# --- Standard library imports ---
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

# --- Third-party imports ---
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from icalendar import Calendar, Event as ICalEvent, vText
from sqlalchemy.ext.asyncio import AsyncSession

# --- Internal imports ---
from app.api.common import market_tz, split_csv, today_in_market
from app.config import settings
from app.database import get_session
from app.models import FeedFetch
from app.services.events_query import query_events
from app.site_config import load_site_config

logger = logging.getLogger(__name__)

# --- Router setup ---
router = APIRouter(prefix="/feeds", tags=["feeds"])


# --- Feed telemetry ---

@lru_cache(maxsize=1)
def _warn_telemetry_salt_missing() -> None:
    """Log the unset-salt warning once per process.

    /feeds/events.ics is the highest-frequency endpoint in the app — calendar clients
    poll it unattended on their own schedule — while a missing TELEMETRY_SALT is a
    static property of the deployment. Warning per request would emit one line per
    poll, forever, for a condition an operator can only fix once.
    """
    logger.warning("TELEMETRY_SALT is unset; feed telemetry disabled (no feed_fetches rows will be written)")


def _resolve_client_ip(request: Request) -> str:
    """The client's remote address, as a stable identifier for distinct-client counting.

    Prefers ``X-Real-IP``, which is what Railway documents as "for identifying client's
    remote IP" (networking/public-networking/specs-and-limits). Note that
    ``X-Forwarded-For`` is *not* in Railway's documented header set at all — only
    ``X-Forwarded-Proto`` and ``X-Forwarded-Host`` are — so it is a fallback for other
    topologies, never the primary source here.

    This function previously read the last ``X-Forwarded-For`` entry, on the reasoning
    that a proxy appends the address it received from and so the final entry is the one
    the edge itself wrote. That holds behind exactly one trusted proxy. Railway routes
    through a multi-hop global edge network, where the last entry is an ephemeral
    internal address that changes per request — measured 2026-08-08 against the
    deployed origin, five polls from one machine with one fixed User-Agent produced
    five distinct hashes, so ``COUNT(DISTINCT client_hash)`` counted polls rather than
    clients and the epic's primary demand proxy was meaningless.

    Both proxy headers are caller-supplied unless an edge overwrites them, so neither
    is trustworthy on a deployment with no proxy in front; that is the same exposure
    the previous implementation carried, and it is bounded by what the value is used
    for (a salted, truncated hash for cohort counting — never authorization).
    """
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    # Fallback for non-Railway deployments. The first entry stays caller-controlled
    # (a client can send "X-Forwarded-For: 9.9.9.9" and have it arrive as
    # "9.9.9.9, <real-ip>"), so take the last non-empty entry, not the first.
    forwarded_for = request.headers.get("x-forwarded-for", "")
    for entry in reversed(forwarded_for.split(",")):
        if entry.strip():
            return entry.strip()
    return request.client.host if request.client else ""


async def record_feed_fetch(
    session: AsyncSession, request: Request, venue_slugs: Optional[list[str]]
) -> None:
    """Best-effort telemetry write: one feed_fetches row per successfully served fetch.

    Never allowed to break the feed response — any failure is caught, the session
    rolled back, and a warning logged; the caller's response is unaffected either way.

    The salt is required unconditionally: an empty TELEMETRY_SALT makes this a no-op
    (plus a one-time warning) rather than accumulate a client_hash that's
    brute-forceable back to a source IP. Deliberately not gated on APP_ENV or any
    other environment variable — the deploy path sets no env vars, so an env-gated
    check would default to the permissive branch in exactly the deployment that
    needs the strict one.

    ``venue_slugs`` is the *parsed* filter (what query_events was actually given),
    not the raw ?venue= string, so the recorded value can never describe a feed
    different from the one served.
    """
    if not settings.TELEMETRY_SALT:
        _warn_telemetry_salt_missing()
        return
    try:
        client_ip = _resolve_client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        digest = hashlib.sha256(f"{settings.TELEMETRY_SALT}{client_ip}|{user_agent}".encode()).hexdigest()
        # Sorted so "a,b" and "b,a" — which serve the same feed, and which the filter
        # UI emits in click order — land in one bucket rather than two.
        venue_filter = ",".join(sorted(venue_slugs)) if venue_slugs else None
        session.add(FeedFetch(client_hash=digest[:16], venue_filter=venue_filter))
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.warning(f"Failed to record feed fetch telemetry: {exc}")


# --- iCal feed endpoint ---

@router.get("/events.ics", response_class=Response)
async def get_ical_feed(
    request: Request,
    venue: Optional[str] = Query(None, description="Comma-separated venue slugs. Omit for all venues."),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Live iCal subscription feed. Add to Apple Calendar, Google Calendar, or Outlook once;
    new shows appear automatically as the scraper finds them."""

    site = load_site_config().site

    # Parsed once and reused for both the query and the telemetry row, so what gets
    # recorded can't drift from what got served.
    venue_slugs = split_csv(venue)

    # Only upcoming events (no historical clutter in subscribers' calendars), via the
    # shared query service. dedup=False: the feed lists every venue's own offering,
    # including cross-venue duplicate listings the calendar collapses. "Today" is the
    # market's calendar date (site.timezone), not the (UTC) server's.
    events = await query_events(
        session,
        start=today_in_market(),
        venue_slugs=venue_slugs,
        dedup=False,
    )

    # --- Build the iCal Calendar object ---

    cal = Calendar()
    # Server PRODID format is domain-only (region-pack epic decision 11); the
    # client "download my shows" export (favorites.js) uses a distinct
    # name+domain form and is not touched here.
    cal.add("prodid", f"-//{site.domain}//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", vText(site.name))
    cal.add("x-wr-caldesc", vText(site.calendar_description))
    cal.add("x-wr-timezone", vText(site.timezone))
    # Suggest clients refresh every 6 hours (matches scraper cadence)
    cal.add("refresh-interval;value=duration", "PT6H")
    cal.add("x-published-ttl", "PT6H")

    now = datetime.now(timezone.utc)

    # --- Serialize each event as an iCal VEVENT component ---

    for event in events:
        venue_obj = event.venue
        iev = ICalEvent()

        # uid_host defaults to domain but Triangle pins a historically-divergent
        # value (decision 8) — subscribers' event UIDs must never change.
        iev.add("uid",     vText(f"{event.id}@{site.uid_host}"))
        iev.add("dtstamp", now)

        # Summary: prefer artist name, fall back to event name
        summary = event.artist or event.name
        iev.add("summary", vText(summary))

        # All-day or timed event — iCal uses DATE vs DATETIME depending on whether time is known
        if event.show_time:
            start = datetime.combine(event.date, event.show_time, tzinfo=market_tz())
            iev.add("dtstart", start)
            # Assume 3-hour show duration when no end time is scraped
            iev.add("dtend",   start + timedelta(hours=3))
        else:
            iev.add("dtstart", event.date)
            iev.add("dtend",   event.date + timedelta(days=1))

        # Location
        if venue_obj:
            iev.add("location", vText(f"{venue_obj.name}, {venue_obj.city}, {site.region_code}"))

        # Description — pack in the useful bits
        desc_parts = []
        if event.name and event.name != summary:
            # Include full event name when it differs from the headline artist
            desc_parts.append(event.name)
        if event.support_artists:
            # support_artists is a list; an empty list is falsy, so the guard above
            # still skips it. Join the names for human-readable display.
            desc_parts.append(f"w/ {', '.join(event.support_artists)}")
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
            # Separate ticket URL onto its own line for readability in calendar apps
            desc_parts.append(f"\n{event.ticket_url}")
        if desc_parts:
            iev.add("description", vText("\n".join(desc_parts)))

        # URL
        if event.ticket_url:
            iev.add("url", event.ticket_url)

        cal.add_component(iev)

    # --- Serialize and return the .ics response ---

    ical_bytes = cal.to_ical()

    # Recorded after serialization succeeds, immediately before the response returns:
    # an INSERT rejected at the DB can't poison the events read, and only successfully
    # served feeds are counted.
    await record_feed_fetch(session, request, venue_slugs)

    return Response(
        content=ical_bytes,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{site.title}.ics"',
            # Cache for 1 hour on CDN/proxies; scraper runs every 6 hours so this is safe
            "Cache-Control": "public, max-age=3600",
        },
    )
