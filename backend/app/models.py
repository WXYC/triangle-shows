"""
SQLAlchemy ORM models defining the database schema for Venue, Event, EventMissState,
ScrapeLog, and FeedFetch.

Role: Shared data layer — imported by database.py (Base), scrapers/manager.py (upsert
logic), and API route handlers. Migrations are managed by Alembic using these definitions.
Requires: app.database (Base), PostgreSQL via asyncpg/SQLAlchemy async.
"""

# --- Imports ---

from datetime import datetime, date, time
from typing import Optional
from sqlalchemy import ARRAY, String, Integer, Float, Text, Date, Time, DateTime, ForeignKey, JSON, Boolean, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base


# --- Constants ---

# Venue.scraper_type for a venue nothing scrapes: a promoter, a festival, a one-off series
# an admin created by hand so that events can hang off something.
#
# A string in an existing column rather than a new boolean, so this needed no migration —
# but it is load-bearing in two places and must not be spelled inline in either.
# ScrapeManager.scrape_all() excludes these venues, without which each one would write a
# failed ScrapeLog on every cycle (there is deliberately no scraper for them), and
# app.api.venues hides them from the public venue list while they have no upcoming events,
# so an empty promoter is not offered as a filter.
MANUAL_SCRAPER_TYPE = "manual"


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


class SubmissionStatus(str, enum.Enum):
    """Where a proposed show is in review. See ShowSubmission."""
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


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
    # Retiring a venue must not be blocked by its series rules. seed_venues() deletes
    # venues listed in REMOVED_SLUGS on every startup via session.delete(), which cascades
    # only through relationships SQLAlchemy knows about — so without this, the first
    # retirement of a venue holding a series override raises ForeignKeyViolation inside the
    # lifespan handler and the container never becomes healthy.
    series_overrides: Mapped[list["SeriesOverride"]] = relationship(
        back_populates="venue", cascade="all, delete-orphan"
    )


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

    # --- Live-music classification (see app/classifier.py) ---
    # Effective flag the calendar and iCal feeds filter on. Materialized (not computed
    # on read) so filtering is a simple `WHERE is_live_music = true`. Recomputed after
    # every scrape by ScrapeManager.reclassify_all() — except on rows an admin has
    # manually overridden (is_manual_override=True), which are left untouched.
    is_live_music: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False)  # True once an admin sets the flag by hand
    # True when an admin created this row by hand rather than a scraper finding it.
    #
    # Distinct from is_manual_override above, which records a hand-set live-music
    # *verdict*. A row can be manually created and auto-classified, or scraped and
    # manually overridden; conflating the two makes either signal unreadable.
    #
    # Load-bearing for the snapshot diff: it tombstones any row inside the scraped date
    # window that the scrape stopped returning, and a hand-added event never is returned
    # (see scrapers/manager.scraper_must_not_delete). Nothing in the scraper path writes
    # this flag — deliberately, because the upsert writes scraper data onto a matched row,
    # so filtering on `source` would lose the protection the first time a venue listed a
    # show that had already been added by hand.
    is_manually_created: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Set by an admin to the id of the surviving row when this event is a duplicate of it
    # (issue #63). Null means "not a duplicate", which is every scraped row.
    #
    # A pointer rather than a `hidden_as_duplicate` boolean, so the judgement stays
    # auditable and undoable: the admin UI can show what was folded into a survivor, and
    # ON DELETE SET NULL means deleting the survivor un-hides this row rather than leaving
    # it hidden behind a row that no longer exists.
    #
    # Exempt from the snapshot diff's tombstone in the same way is_manually_created is
    # (upstream's reconcile would otherwise have deleted it, destroying the audit trail
    # the column exists to keep). See scrapers/manager.scraper_must_not_delete.
    #
    # No self-referential relationship() yet: nothing reads the pointer until #63 adds the
    # admin controls, and an unused relationship on a self-join is a configuration to
    # maintain rather than a convenience.
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    classification_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # human-readable why, e.g. "recurring: 6 dates", "keyword: trivia", "manual"
    # Set when an admin confirms the current classification is correct; the admin list
    # hides approved events by default. Records agreement with a *verdict*, not the
    # event: reclassify_all() clears this whenever it changes is_live_music, so a
    # re-classified event returns to the review queue rather than staying hidden.
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue: Mapped["Venue"] = relationship(back_populates="events")

    # Declared here as well as in migration 0013 so `alembic revision --autogenerate`
    # compares equal against a live database and does not propose dropping either one.
    # The index is partial because the read paths ask `duplicate_of_id IS NULL`, which
    # matches nearly every row and is better served by a scan; the lookup that needs an
    # index is the admin's "what was folded into this row", which only touches non-nulls.
    #
    # Identity is per-venue: external_id/source_url uniqueness is only assumed within a
    # venue (e.g. VenuePilot ids are small integers that can collide across venues).
    __table_args__ = (
        Index("uq_events_venue_source_key", "venue_id", "source_key", unique=True),
        CheckConstraint(
            "duplicate_of_id IS NULL OR duplicate_of_id <> id",
            name="ck_events_duplicate_of_not_self",
        ),
        Index(
            "ix_events_duplicate_of_id",
            "duplicate_of_id",
            postgresql_where=text("duplicate_of_id IS NOT NULL"),
        ),
    )


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


class FeedFetch(Base):
    """Append-only server-side telemetry: one row per successfully served
    GET /feeds/events.ics (app.api.feeds.record_feed_fetch).

    Privacy-minimal by design — no raw IP or user-agent is ever stored, only a
    salted, truncated hash (client_hash). The composite (fetched_at, client_hash)
    index serves every report query: a time-range scan (month window, trailing 28
    days) with COUNT(DISTINCT client_hash) on top, so a standalone fetched_at
    index would be redundant.
    """
    __tablename__ = "feed_fetches"

    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    client_hash: Mapped[str] = mapped_column(String(16))
    # The parsed ?venue= filter, comma-joined in sorted order; NULL whenever the full
    # calendar was served (param absent, or present with no usable slugs). Normalized
    # rather than raw so the NULL/non-NULL split and any GROUP BY describe the feed
    # that was actually served, not the spelling the client happened to use.
    venue_filter: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_feed_fetches_fetched_at_client_hash", "fetched_at", "client_hash"),)


class SeriesOverride(Base):
    """An admin decision that applies to a whole recurring series, not one event.

    A recurring series (weekly karaoke, a standing jazz jam, a monthly comedy
    showcase, ...) produces many Event rows over time, and new ones appear on each
    scrape. A per-event override can't cover future instances, so a series-level
    rule is keyed by (venue_id, normalized_name) — the same key the recurrence
    detector groups on (see app.classifier.normalize_series_name). During
    ScrapeManager.reclassify_all(), a matching rule forces is_live_music for every
    event in the series, including instances scraped later.

    Precedence, most specific first: a per-event Event.is_manual_override wins over
    a SeriesOverride, which in turn wins over automatic classification.
    """
    __tablename__ = "series_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ondelete="CASCADE" as well as the ORM relationship on Venue: the relationship covers
    # session.delete(), the database constraint covers everything else (a manual DELETE, a
    # future bulk query that bypasses the ORM's cascade).
    venue_id: Mapped[int] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), index=True
    )
    normalized_name: Mapped[str] = mapped_column(String(200))  # matches app.classifier.normalize_series_name(event.name)
    display_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # a readable example name for the admin UI
    is_live_music: Mapped[bool] = mapped_column(Boolean)  # the value forced onto every event in the series
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # optional admin note explaining the decision
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue: Mapped["Venue"] = relationship(back_populates="series_overrides")

    # One rule per (venue, series name); the admin endpoint upserts on this key.
    __table_args__ = (
        UniqueConstraint("venue_id", "normalized_name", name="uq_series_override_key"),
    )


class ShowSubmission(Base):
    """A show someone proposed through /new-shows-form, waiting for an admin.

    A table of its own rather than Event rows with a "pending" marker, and that is the
    point of it. Every public read path — the calendar feed, the iCal feed, the venue
    list's counts, the duplicate queue, reclassify, reconcile — reads `events` and would
    each have to remember to exclude a pending row; one that forgot would publish an
    unreviewed show. A pending Event would also occupy the unique hash a scraper matches
    on, so a venue listing the same show would quietly adopt the unreviewed row. Kept
    here, a submission cannot reach the site by any path except approval, which builds
    the Venue and Event through the same code an admin's own hand-add uses.

    The proposed venue is either an existing one (venue_id) or fields for a new one
    (new_venue_*), which approval creates as a manual venue. Neither is enforced by a
    CHECK: venue_id is ON DELETE SET NULL, and a constraint requiring one or the other
    would make deleting a venue fail on any pending submission that pointed at it. The
    API validates instead, and the admin can repoint a submission whose venue vanished.

    Rows are kept after review, approved or rejected, so a submitter can see what became
    of what they sent, and so an approved event can say who proposed it.
    """
    __tablename__ = "show_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default=SubmissionStatus.pending.value, index=True)
    # The Cloudflare Access identity that submitted it — an email for a person.
    submitted_by: Mapped[str] = mapped_column(String(320), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # --- Venue: an existing one, or a proposed new one ---
    venue_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("venues.id", ondelete="SET NULL"), nullable=True
    )
    new_venue_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    new_venue_city: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_venue_website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # --- The proposed event; same shapes as the Event columns they become ---
    name: Mapped[str] = mapped_column(String(500))
    artist: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    support_artists: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    date: Mapped[date] = mapped_column(Date)
    doors_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    show_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    ticket_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    price_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    age_restriction: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_live_music: Mapped[bool] = mapped_column(Boolean, default=True)
    # Free text to the admin: where they heard about it, anything that does not fit a field.
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Review ---
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # The event approval created. SET NULL so deleting that event later does not take
    # the record of the submission with it.
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )

    venue: Mapped[Optional["Venue"]] = relationship()
