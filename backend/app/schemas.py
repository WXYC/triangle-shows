"""
Pydantic response models used to serialize database ORM objects into JSON for the API.

Role: These schemas sit between the SQLAlchemy models (models.py) and the FastAPI
route handlers (app/api/). Each API endpoint returns one of these models, which
controls exactly what fields are exposed to clients and how they are typed.
Requires: models.py (ORM objects are converted via from_attributes=True),
          pydantic (validated automatically by FastAPI on response).
"""

# --- Imports ---

from pydantic import AfterValidator, BaseModel
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
    support_artists: Optional[str] = None
    date: date
    doors_time: Optional[time] = None
    show_time: Optional[time] = None
    ticket_url: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    image_url: Optional[str] = None
    genre: Optional[str] = None
    subgenre: Optional[str] = None
    status: str
    age_restriction: Optional[str] = None
    description: Optional[str] = None
    source: str
    # Last-modified timestamp; changes only when a scrape actually modifies the row.
    updated_at: Optional[UTCDateTime] = None

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
