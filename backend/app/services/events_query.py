"""
Shared events query + cross-venue de-duplication.

Role: The single place that fetches events, applies the common filters, and
collapses cross-venue duplicates. Every read surface calls this — the FullCalendar
web feed, the paginated list endpoint, the iCal feed (dedup=False), and the
surface-neutral /api/v1 endpoints — so they all see the *same* set of events.
This logic previously lived only inside the FullCalendar handler, which meant the
plain `/api/events` list returned un-deduplicated rows; moving it here fixes that
inconsistency.

Requires: async PostgreSQL session (app.database), Event/Venue ORM models (app.models).
"""

import unicodedata
from datetime import date as date_cls
from typing import Optional, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Event, Venue


def _normalize_label(label: str) -> str:
    """Case-fold, strip diacritics, and keep only letters/digits (any script).

    NFKD decomposition splits accented characters into base + combining mark, and
    the isalnum() filter then drops the marks (and all punctuation/whitespace), so
    "Hermanos Gutiérrez" and "Hermanos Gutierrez" normalize identically while
    non-Latin names ("Молчат Дома", "坂本龍一") keep their characters instead of
    collapsing to an empty string.
    """
    decomposed = unicodedata.normalize("NFKD", label.casefold())
    return "".join(ch for ch in decomposed if ch.isalnum())


def _escape_like(term: str) -> str:
    """Escape LIKE/ILIKE wildcards so user-supplied text matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _dedupe_key(event: Event) -> tuple:
    """(date, normalized artist-or-name) — the identity used to detect duplicates.

    Normalizing means minor punctuation/spacing/diacritic differences between
    sources don't defeat the match. A label with no letters or digits at all
    (e.g. the band "!!!") yields a per-event key, so symbol-only names on the
    same date are never treated as duplicates of each other.
    """
    label = event.artist or event.name
    norm = _normalize_label(label)
    if not norm:
        return (event.date, f"\x00id:{event.id}")
    return (event.date, norm)


def _completeness_score(event: Event) -> int:
    """How rich an event record is (0–3): prefer entries with art, tickets, and a price."""
    return bool(event.image_url) + bool(event.ticket_url) + (event.price_min is not None)


def dedupe_cross_venue(events: Sequence[Event]) -> list[Event]:
    """Collapse events sharing a (date, artist/name) key, keeping the best record.

    The first record for a key wins by default; a record from a *different* venue
    replaces it only when it is strictly more complete. Same-key records from the
    *same* venue therefore collapse to the first one seen. Pass events in a stable
    order (``query_events`` orders by date then id) so the tiebreak is deterministic.
    Input order is otherwise preserved in the output.
    """
    best: dict[tuple, Event] = {}
    best_score: dict[tuple, int] = {}
    for event in events:
        key = _dedupe_key(event)
        score = _completeness_score(event)
        if key not in best:
            best[key] = event
            best_score[key] = score
        elif event.venue_id != best[key].venue_id and score > best_score[key]:
            best[key] = event
            best_score[key] = score
    kept = {ev.id for ev in best.values()}
    return [e for e in events if e.id in kept]


async def query_events(
    session: AsyncSession,
    *,
    start: Optional[date_cls] = None,
    end: Optional[date_cls] = None,
    cities: Optional[Sequence[str]] = None,
    sizes: Optional[Sequence[str]] = None,
    venue_slugs: Optional[Sequence[str]] = None,
    search: Optional[str] = None,
    genre: Optional[str] = None,
    status: Optional[str] = None,
    dedup: bool = True,
) -> list[Event]:
    """Fetch events (with venue eagerly loaded), filtered and optionally de-duplicated.

    All filters are ANDed; ``search``/``genre`` match literally (LIKE wildcards in the
    input are escaped). Results are ordered by (date, id): date is the meaningful sort
    for a calendar, and id is a stable tiebreak that makes de-duplication deterministic.

    De-duplication happens *after* filtering, so it is relative to the requested set:
    a venue filter that excludes the winning record of a duplicate pair will surface
    the record that an unfiltered query suppresses. That is intentional — a venue's
    own listing should show that venue's record. Set ``dedup=False`` to get every
    matching row (e.g. the iCal feed, which lists all venue offerings).
    """
    conditions = []
    if start is not None:
        conditions.append(Event.date >= start)
    if end is not None:
        conditions.append(Event.date <= end)
    if cities:
        conditions.append(Venue.city.in_(list(cities)))
    if sizes:
        conditions.append(Venue.size_category.in_(list(sizes)))
    if venue_slugs:
        conditions.append(Venue.slug.in_(list(venue_slugs)))
    if search:
        term = f"%{_escape_like(search)}%"
        conditions.append(Event.name.ilike(term, escape="\\") | Event.artist.ilike(term, escape="\\"))
    if genre:
        conditions.append(Event.genre.ilike(f"%{_escape_like(genre)}%", escape="\\"))
    if status:
        conditions.append(Event.status == status)

    # selectinload fetches the ~20 venues in one extra small query instead of
    # repeating venue columns onto every event row (and needing unique()).
    query = select(Event).options(selectinload(Event.venue))
    # Only JOIN venues when a venue-level filter is present.
    if cities or sizes or venue_slugs:
        query = query.join(Event.venue)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(Event.date, Event.id)

    result = await session.execute(query)
    events = list(result.scalars().all())

    if dedup:
        events = dedupe_cross_venue(events)
    return events
