"""
SQLAlchemy ORM models defining the database schema for Venue, Event, and ScrapeLog.

Role: Shared data layer — imported by database.py (Base), scrapers/manager.py (upsert
logic), and API route handlers. Migrations are managed by Alembic using these definitions.
Requires: app.database (Base), PostgreSQL via asyncpg/SQLAlchemy async.
"""

# --- Imports ---

from datetime import datetime, date, time
from typing import Optional
from sqlalchemy import ARRAY, String, Integer, Float, Text, Date, Time, DateTime, ForeignKey, JSON, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base


# --- Enums ---

class EventStatus(str, enum.Enum):
    """Possible ticket/availability states for an event."""
    on_sale = "on_sale"
    sold_out = "sold_out"
    cancelled = "cancelled"
    free = "free"


class ScrapeStatus(str, enum.Enum):
    """Lifecycle states written to ScrapeLog during and after a scrape run."""
    running = "running"
    success = "success"
    failed = "failed"


# --- Models ---

class Venue(Base):
    """A physical concert venue in the Triangle area."""
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)  # URL-safe identifier, e.g. "cats-cradle"
    city: Mapped[str] = mapped_column(String(50))
    capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    size_category: Mapped[str] = mapped_column(String(20))  # small, medium, large
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ticketmaster_venue_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # used by Ticketmaster scraper
    scraper_type: Mapped[str] = mapped_column(String(50))  # selects which scraper class to use
    scraper_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # extra params passed to the scraper (e.g. API keys, URL overrides)
    color: Mapped[str] = mapped_column(String(7), default="#6366f1")  # hex color shown on the calendar
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    events: Mapped[list["Event"]] = relationship(back_populates="venue", cascade="all, delete-orphan")
    scrape_logs: Mapped[list["ScrapeLog"]] = relationship(back_populates="venue", cascade="all, delete-orphan")


class Event(Base):
    """A single concert or show at a venue."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # ID from the source system (e.g. Ticketmaster event ID)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    artist: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # Best-effort cleaned performer (issue #18): derived at upsert time from the
    # source's structured performer data when available, else heuristically from
    # name (scrapers/headliner.py). Null when nothing performer-like can be
    # extracted (karaoke nights, framing-only billings) or for rows not rescraped
    # since the column landed. Unlike artist, this tracks the current name/performer
    # deterministically — the upsert overwrites it rather than merge-preserving it.
    headliner: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # Support/opening acts as a lossless list, one name per element. Stored as a
    # Postgres text[] (not a comma-joined string) so names that themselves contain
    # commas ("Earth, Wind & Fire") survive as a single element instead of splitting
    # into fake acts. Empty list (never NULL) when the billing names no support.
    support_artists: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"), default=list
    )
    date: Mapped[date] = mapped_column(Date, index=True)
    doors_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    show_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    ticket_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    price_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    subgenre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.on_sale.value)
    age_restriction: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50))  # scraper name that produced this event, e.g. "ticketmaster"
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    # Reconciliation key for the URL tier: source_url with scheme/host/fragment/tracking
    # params stripped (identity.normalize_source_url). Stored for every row that has a
    # source_url, regardless of which identity tier won.
    normalized_source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True, index=True)
    # Content hash (venue_slug | date | normalized name). No longer unique: identity
    # uniqueness lives in (venue_id, source_key), and duplicate hashes can transiently
    # exist (e.g. an event rescheduled onto a date where a duplicate listing was once
    # ingested). Still indexed — it's the tier-3 reconciliation key and in-batch dedup key.
    hash: Mapped[str] = mapped_column(String(64), index=True)
    # The event's canonical external identity, tier-prefixed: "ext:<external_id>",
    # "url:<normalized_source_url>", or "hash:<hash>" (identity.derive_source_key).
    # External contract — consumers (WXYC Backend-Service) key on it; ext/url keys are
    # stable across rename/reschedule, hash keys are not. 1100 = prefix + String(1000) URL.
    source_key: Mapped[str] = mapped_column(String(1100), nullable=False)
    # Soft tombstone: when the venue stopped advertising this event (observation, not
    # interpretation — status is never inferred from it, and nothing hard-deletes on it).
    # Stamped/cleared by the scrape diff (manager.py), which also bumps updated_at.
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Identity is per-venue: external_id/source_url uniqueness is only assumed within a
    # venue (e.g. VenuePilot ids are small integers that can collide across venues).
    __table_args__ = (Index("uq_events_venue_source_key", "venue_id", "source_key", unique=True),)

    venue: Mapped["Venue"] = relationship(back_populates="events")


class EventMissState(Base):
    """Miss-streak bookkeeping for the vanished-event diff (manager.py).

    One row per event currently missing from its venue's scrape snapshots; the row's
    existence IS the streak ("missed at least once"), so a further miss on a later
    day within the staleness window tombstones — no counter needed. Kept off the
    events row on purpose: events.updated_at must move only for client-visible
    changes, and any UPDATE to an events row would fire its onupdate stamp. No ORM
    relationship to Event is declared — the diff reads and writes this table by
    explicit query, and deletion is handled at the database level (the FK cascades
    so the scheduler's Core delete(Event) cleanup can't hit FK violations).
    """
    __tablename__ = "event_miss_state"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    # Triangle calendar date (America/New_York) of the most recent miss; the diff
    # records at most one miss per event per calendar day.
    last_miss_date: Mapped[date] = mapped_column(Date)


class ScrapeLog(Base):
    """Audit record written by the scrape manager for each venue scrape attempt."""
    __tablename__ = "scrape_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    scraper_type: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # null while still running
    status: Mapped[str] = mapped_column(String(20), default=ScrapeStatus.running.value)
    events_found: Mapped[int] = mapped_column(Integer, default=0)   # total events returned by the scraper
    events_created: Mapped[int] = mapped_column(Integer, default=0)  # net-new events inserted
    events_updated: Mapped[int] = mapped_column(Integer, default=0)  # existing events that were updated
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # populated on failure
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    venue: Mapped["Venue"] = relationship(back_populates="scrape_logs")
