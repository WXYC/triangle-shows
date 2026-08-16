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

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import (
    event_to_response,
    get_event,
    health_check,
    list_venues,
    split_csv,
    today_in_market,
)
from app.config import settings
from app.database import get_session
from app.models import EventStatus, ScrapeLog, Venue
from app.schemas import EventResponse, HealthResponse, ScraperHealthResponse, VenueResponse
from app.services.events_query import query_events
from app.services.scrape_health import evaluate_venue_health
from app.site_config import SiteConfig, load_site_config

# Row cap for the per-venue scrape_logs query below. Must comfortably span
# scrape_health.BASELINE_WINDOW_DAYS (30) at the busiest group's cadence (the indie
# group's 3 runs/day -> 90 rows for 30 days); 120 leaves headroom. Ordering by
# started_at DESC (matching the ix_scrape_logs_venue_id_started_at composite index
# added alongside this endpoint) means row 0 is always the true most recent attempt
# regardless of how old it is -- a venue silent for the whole window still resolves
# to a real "stale"/"critical" verdict rather than misreporting "unknown".
_RECENT_SCRAPE_LOG_LIMIT = 120

# --- Router ---

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _expand_city_aliases(cities: Optional[list[str]]) -> Optional[list[str]]:
    """Expand a display grouping (e.g. "Chapel Hill-Carrboro") into its member
    municipalities. venues.city holds real towns, never a grouping label; the
    grouping is a query-time alias sourced from the active region's
    site.toml [city_groups] (region-pack epic decision 9 — replaces the former
    CITY_ALIASES literal), so pre-existing links keep working across regions
    without a code change.
    """
    if cities is None:
        return None
    city_groups = load_site_config().city_groups
    return [c for token in cities for c in city_groups.get(token, (token,))]


# --- Endpoints ---

@router.get(
    "/events",
    response_model=list[EventResponse],
    summary="List de-duplicated events for a date window",
)
async def list_events(
    start: Optional[date] = Query(None, description="ISO date (YYYY-MM-DD), inclusive lower bound. Defaults to today in the region's market timezone when end is also omitted; pass an explicit value to query history."),
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
    region's market timezone so a bare request returns upcoming events rather than the
    entire history; an explicit `start` (or an `end` on its own) queries history. Malformed
    dates are rejected with a 422. De-duplication semantics live in
    app.services.events_query.query_events; pass `dedup=false` to see every stored row.
    """
    if start is None and end is None:
        start = today_in_market()
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


@router.get(
    "/site",
    response_model=SiteConfig,
    response_model_by_alias=False,
    summary="Region site manifest (branding, identity, presentation config)",
)
async def get_site() -> SiteConfig:
    """The active region's site manifest — branding, iCal/head identity, city
    grouping, and frontend presentation config (palettes, links, subdomains),
    as declared in the region's site.toml (region-pack epic, issue #62/#64).
    Additive to the v1 contract: no existing endpoint's response shape changes.
    """
    return load_site_config()


@router.get(
    "/health/scrapers",
    response_model=list[ScraperHealthResponse],
    summary="Per-venue scrape-health verdicts",
)
async def get_scraper_health(session: AsyncSession = Depends(get_session)) -> list[ScraperHealthResponse]:
    """Every venue's current scrape-health verdict (issue #86 part 1: detection and
    exposure), derived from recent app.models.ScrapeLog history by the pure evaluator
    in app.services.scrape_health. `ok | warning | critical | unknown`, each with a
    `signal` tag and a human-readable `detail` — already scrubbed of any embedded
    query string, since this endpoint is unauthenticated and ScrapeLog.error_message
    can otherwise carry the Ticketmaster API key.

    Deliberately implemented here rather than in app.api.common: this is an
    operations surface, not part of the client-agnostic event/venue contract, and it
    has no deprecated-router twin to share a handler with (see backend/README.md's
    "API contracts" section — precedent: GET /api/v1/site).

    Staleness (one of the evaluator's three signals) is only meaningful when
    something is actually scheduled to run it, so this passes
    settings.ENABLE_SCHEDULER through unchanged rather than assuming True — a
    freshly-seeded dev database or a region not yet switched on would otherwise
    report every venue "critical" for having no recent scrapes it was never
    supposed to have.
    """
    now = datetime.utcnow()
    venues = (await session.execute(select(Venue).order_by(Venue.city, Venue.name))).scalars().all()

    verdicts = []
    for venue in venues:
        logs = (
            await session.execute(
                select(ScrapeLog)
                .where(ScrapeLog.venue_id == venue.id, ScrapeLog.started_at <= now)
                .order_by(ScrapeLog.started_at.desc())
                .limit(_RECENT_SCRAPE_LOG_LIMIT)
            )
        ).scalars().all()
        verdict = evaluate_venue_health(venue, logs, now=now, evaluate_staleness=settings.ENABLE_SCHEDULER)
        verdicts.append(ScraperHealthResponse.model_validate(verdict))
    return verdicts
