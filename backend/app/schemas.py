"""
Pydantic response models used to serialize database ORM objects into JSON for the API.

Role: These schemas sit between the SQLAlchemy models (models.py) and the FastAPI
route handlers (app/api/). Each API endpoint returns one of these models, which
controls exactly what fields are exposed to clients and how they are typed.
Requires: models.py (ORM objects are converted via from_attributes=True),
          pydantic (validated automatically by FastAPI on response).
"""

# --- Imports ---

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field
from datetime import date, time, datetime, timezone
from typing import Annotated, Optional


# --- Shared field types ---

def _assume_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes so they serialize with an explicit offset.

    The ORM stores naive datetime.utcnow() values; without this, timestamps
    serialize as bare local-looking strings that clients misparse as local time.
    Already-aware values pass through untouched.
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


# A datetime stored as naive UTC, always serialized with an explicit UTC offset.
UTCDateTime = Annotated[datetime, AfterValidator(_assume_utc)]


def _none_if_not_absolute_http_url(value):
    """Coerce a non-absolute-http(s) ``ticket_url``/``image_url`` to ``None``.

    Both fields are scraper-sourced from 21+ third-party venue sites (see
    ``app.scrapers.base.ScrapedEvent.__post_init__``, which normalizes them the
    same way at ingestion) and are read by the web client, which renders them
    into HTML attributes, and by the Backend-Service "On Tour" consumer. This is
    a second, independent gate at the API boundary — it protects both against
    any row written before that ingestion-time normalization existed and against
    anything that reaches the database by a path other than a scraper. A
    relative path, a ``javascript:``/``data:`` scheme, or any other non-string
    value must never reach a consumer as-is — but a single malformed field must
    not fail the whole event, so this normalizes rather than raises
    (WXYC/triangle-shows#94).
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.lower().startswith(("http://", "https://")) else None


# A ticket_url/image_url, normalized to an absolute http(s) string or None.
OptionalHttpUrl = Annotated[Optional[str], BeforeValidator(_none_if_not_absolute_http_url)]


# --- Venue Schema ---

class VenueResponse(BaseModel):
    """Venue data returned by GET /api/venues."""
    id: int
    name: str
    slug: str
    city: str
    capacity: Optional[int] = None
    size_category: str
    website: Optional[str] = None
    color: str  # Hex color used for calendar event styling per venue

    # Allow constructing directly from a SQLAlchemy Venue ORM instance
    model_config = {"from_attributes": True}


# --- Event Schema ---

class EventResponse(BaseModel):
    """Full event detail returned by the events list endpoint."""
    id: int
    venue_id: int
    name: str
    artist: Optional[str] = None
    # Best-effort cleaned performer (issue #18): the headliner with support acts
    # ("w/ …", "// …", "feat. …"), leading ticketing tags ("(SOLD OUT)", "(18+)"),
    # and framing ("An Evening With:", "… Presents:", "Tribute to …") stripped —
    # taken from structured source data (schema.org Event.performer, Ticketmaster
    # attractions) when available, else heuristically from the name. Null when no
    # performer can be extracted (karaoke nights, listening parties) or the row
    # has not been rescraped since the field was introduced. `name` remains the
    # full display title and `artist` keeps its historical semantics, unchanged.
    headliner: Optional[str] = None
    # Support/opening acts, one name per element. Serialized as a JSON array on both
    # /api/v1/events and the deprecated /api/events; an empty array (never null) when
    # the billing names no support. Lossless — a name containing a comma stays one
    # element. Joined only for human display (iCal, web modal).
    support_artists: list[str] = Field(default_factory=list)
    date: date
    doors_time: Optional[time] = None
    show_time: Optional[time] = None
    ticket_url: OptionalHttpUrl = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    image_url: OptionalHttpUrl = None
    genre: Optional[str] = None
    subgenre: Optional[str] = None
    status: str
    age_restriction: Optional[str] = None
    description: Optional[str] = None
    source: str
    # Stable per-event identity, tier-prefixed (ext:/url:/hash:) — the key external
    # consumers reconcile on. ext:/url: keys survive renames and reschedules; hash:
    # keys do not (see the source_key contract section in backend/README.md).
    source_key: str
    # Last-modified timestamp; changes only when a scrape actually modifies the row.
    updated_at: Optional[UTCDateTime] = None
    # Soft tombstone: when the venue stopped advertising this event; null for live
    # rows. List endpoints exclude tombstoned events unless include_removed=true;
    # the detail endpoint always resolves them by id. Observation, not
    # interpretation — status is never inferred from a delisting.
    removed_at: Optional[UTCDateTime] = None

    # Denormalized venue fields — joined in the query so clients don't need
    # a separate /api/venues request to display venue info alongside events
    venue_name: Optional[str] = None
    venue_slug: Optional[str] = None
    venue_city: Optional[str] = None
    venue_color: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Paginated Event List Schema ---

class EventListResponse(BaseModel):
    """Wrapper for paginated event results."""
    events: list[EventResponse]
    total: int
    page: int
    per_page: int
    pages: int


# --- Health Check Schema ---

class HealthResponse(BaseModel):
    """Response for GET /api/health -- reports system and scrape status."""
    status: str
    event_count: int
    venue_count: int
    last_scrape: Optional[UTCDateTime] = None  # None if no scrape has run yet
    version: Optional[str] = None


# --- Scrape Health Schema ---

class ScraperHealthResponse(BaseModel):
    """One venue's verdict, returned by GET /api/v1/health/scrapers (issue #86 part 1).

    Deliberately excludes scraper_type: the public contract keeps internal scraping
    machinery out of response bodies (the same rule VenueResponse follows, pinned by
    test_openapi.py). `detail` may name a platform in prose where it aids triage
    without widening the schema. It comes from app.services.scrape_health, already
    scrubbed of any embedded query string (which can carry the Ticketmaster API key) --
    this endpoint is unauthenticated, so raw ScrapeLog.error_message must never reach
    it unredacted.
    """
    venue_slug: str
    status: str  # "ok" | "warning" | "critical" | "unknown"
    signal: Optional[str] = None
    detail: Optional[str] = None
    last_success_at: Optional[UTCDateTime] = None
    last_attempt_at: Optional[UTCDateTime] = None

    model_config = {"from_attributes": True}
