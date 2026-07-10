"""
Shared events query + cross-venue de-duplication.

Role: The single place that fetches events, applies the common filters, and
collapses cross-venue duplicates. Every read surface calls this — the surface-neutral
/api/v1 endpoints, the deprecated paginated `/api/events` list, and the iCal feed
(dedup=False) — so they all see the *same* set of events. This logic once lived only
inside the web calendar's feed handler, which meant the plain `/api/events` list
returned un-deduplicated rows; moving it here fixed that inconsistency.

Requires: async PostgreSQL session (app.database), Event/Venue ORM models (app.models).
"""

import unicodedata
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Event, Venue


def _normalize_label(label: str) -> str:
    """Case-fold, strip diacritics/punctuation, and keep letters/digits of any script.

    NFKD decomposition splits accented characters into base + combining mark and the
    category filter drops the marks, so "Hermanos Gutiérrez" and "Hermanos Gutierrez"
    normalize identically, while non-Latin names ("Молчат Дома", "坂本龍一") keep their
    characters instead of collapsing to an empty string. Modifier letters (category Lm,
    e.g. the U+02BC apostrophe in "OʼConnor") are excluded so apostrophe-style variants
    of the same name still match.
    """
    decomposed = unicodedata.normalize("NFKD", label.casefold())
    filtered = "".join(
        ch for ch in decomposed
        if (category := unicodedata.category(ch))[0] in "LN" and category != "Lm"
    )
    # Second casefold: NFKD can emit cased letters from compatibility characters
    # (e.g. "№" decomposes to "No") even after the input was folded.
    return filtered.casefold()


def _dedupe_key(event: Event) -> tuple:
    """(date, normalized artist-or-name) — the identity used to detect duplicates.

    Normalizing means minor punctuation/spacing/diacritic differences between sources
    don't defeat the match. A label with no letters or digits at all (e.g. the band
    "!!!") has no comparable identity, so those events are keyed by id — i.e. exempt
    from de-duplication entirely; showing a duplicate beats hiding a real show.
    """
    label = event.artist or event.name
    norm = _normalize_label(label)
    if not norm:
        return (event.date, event.id)  # int key can never equal a normalized str key
    return (event.date, norm)


def _completeness_score(event: Event) -> int:
    """How rich an event record is (0–3): prefer entries with art, tickets, and a price."""
    return bool(event.image_url) + bool(event.ticket_url) + (event.price_min is not None)


def _pick_winners(events: Sequence[Event]) -> dict[tuple, Event]:
    """One winner per dedupe key, among records of equal liveness.

    The first record for a key wins by default; a record from a venue *other than the
    first-seen venue* replaces it when strictly more complete. Comparing against the
    first-seen venue (not the current winner) keeps the invariant that records from
    the first venue can only ever contribute their first-seen row — later, richer
    rows from that same venue never displace a cross-venue winner.
    """
    best: dict[tuple, Event] = {}
    best_score: dict[tuple, int] = {}
    first_venue: dict[tuple, int] = {}
    for event in events:
        key = _dedupe_key(event)
        score = _completeness_score(event)
        if key not in best:
            best[key] = event
            best_score[key] = score
            first_venue[key] = event.venue_id
        elif event.venue_id != first_venue[key] and score > best_score[key]:
            best[key] = event
            best_score[key] = score
    return best


def dedupe_cross_venue(events: Sequence[Event]) -> list[Event]:
    """Collapse events sharing a (date, normalized artist/name) key, keeping the best.

    Liveness dominates completeness: records are partitioned into live and tombstoned
    (``removed_at`` set), winners are picked within each partition by the completeness
    rules in ``_pick_winners``, and a tombstoned winner surfaces only for keys with no
    live record at all. So a tombstoned record can never displace a live one however
    rich it is, and a live record always beats a tombstoned incumbent regardless of
    score or venue — otherwise a consumer passing ``include_removed=true`` would see
    "removed" for a show that is still on (the same-venue rename shape makes this
    routine: old row tombstones, new row lives, both share the key). Pass events in a
    stable order (``query_events`` orders by date then id) so the outcome is
    deterministic. Input order is otherwise preserved in the output.
    """
    live_best = _pick_winners([e for e in events if e.removed_at is None])
    tombstoned_best = _pick_winners(
        [e for e in events if e.removed_at is not None and _dedupe_key(e) not in live_best]
    )
    kept = {ev.id for ev in live_best.values()} | {ev.id for ev in tombstoned_best.values()}
    return [e for e in events if e.id in kept]


async def query_events(
    session: AsyncSession,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    cities: Optional[Sequence[str]] = None,
    sizes: Optional[Sequence[str]] = None,
    venue_slugs: Optional[Sequence[str]] = None,
    search: Optional[str] = None,
    genre: Optional[str] = None,
    status: Optional[str] = None,
    dedup: bool = True,
    include_removed: bool = False,
) -> list[Event]:
    """Fetch events (with venue eagerly loaded), filtered and optionally de-duplicated.

    All filters are ANDed; ``search``/``genre`` are case-insensitive substring matches
    with LIKE wildcards in the input matched literally. A venue-level filter passed as
    an empty list means "matches nothing" (None means "no filter"). Results are ordered
    by (date, id): date is the meaningful sort for a calendar, and id is a stable
    tiebreak that makes de-duplication deterministic.

    De-duplication happens *after* filtering, so it is relative to the requested set:
    a venue filter that excludes the winning record of a duplicate pair will surface
    the record that an unfiltered query suppresses. That is intentional — a venue's
    own listing should show that venue's record. Set ``dedup=False`` to get every
    matching row (e.g. the iCal feed, which lists all venue offerings).
    """
    conditions = []
    # Soft-tombstoned events (removed_at set: the venue no longer advertises them)
    # are excluded by default so every list surface inherits the exclusion; pass
    # include_removed=True to see them (mirror-style consumers, /api/v1 opt-out).
    if not include_removed:
        conditions.append(Event.removed_at.is_(None))
    if start is not None:
        conditions.append(Event.date >= start)
    if end is not None:
        conditions.append(Event.date <= end)
    if cities is not None:
        conditions.append(Venue.city.in_(list(cities)))
    if sizes is not None:
        conditions.append(Venue.size_category.in_(list(sizes)))
    if venue_slugs is not None:
        conditions.append(Venue.slug.in_(list(venue_slugs)))
    if search:
        # icontains(autoescape=True) wraps the term in %...% and escapes %/_ so user
        # input matches literally.
        conditions.append(
            Event.name.icontains(search, autoescape=True)
            | Event.artist.icontains(search, autoescape=True)
        )
    if genre:
        conditions.append(Event.genre.icontains(genre, autoescape=True))
    if status:
        conditions.append(Event.status == status)

    # selectinload fetches the ~20 venues in one extra small query instead of
    # repeating venue columns onto every event row (and needing unique()).
    query = select(Event).options(selectinload(Event.venue))
    # Only JOIN venues when a venue-level filter is present.
    if cities is not None or sizes is not None or venue_slugs is not None:
        query = query.join(Event.venue)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(Event.date, Event.id)

    result = await session.execute(query)
    events = result.scalars().all()

    if dedup:
        events = dedupe_cross_venue(events)
    return events
