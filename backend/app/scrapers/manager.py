"""
Scrape orchestrator: runs all venue scrapers, deduplicates results by hash,
upserts events + scrape logs, and diffs each venue's snapshot against stored
events to maintain the vanished-event signal (Event.removed_at tombstones and
their event_miss_state streak bookkeeping).

Role: Triggered by POST /api/scrape (called by the scheduler every 6 hours or
by Cloud Scheduler). Sits between the individual scrapers and the database —
it owns the fan-out, error isolation, the upsert, the snapshot diff, and the
per-venue write serialization (advisory lock).
Requires: TICKETMASTER_API_KEY (via app.config.settings), async PostgreSQL
session, and all scraper modules in app.scrapers/.
"""

# --- Imports ---
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.market_time import today_in_triangle
from app.models import Venue, Event, EventMissState, ScrapeLog
from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.headliner import extract_headliner
from app.scrapers.identity import (
    UrlIdentityVerdict,
    derive_source_key,
    normalize_source_url,
    scraper_class,
    url_identity_verdict,
)
from app.scrapers.ticketmaster import TicketmasterScraper

logger = logging.getLogger(__name__)

# A miss streak with no observation for longer than this restarts instead of
# supplying the tombstoning second miss: two misses separated by a long unobserved
# gap (e.g. the event sat beyond every scrape's horizon for weeks) are isolated
# glitches, not "delisted across two scrape days".
MISS_STREAK_MAX_GAP_DAYS = 7

# When at least this many in-window events vanish in one scrape AND they are the
# majority of the venue's in-window calendar, treat the page as degraded (bot
# challenge, half-rendered listing, broken parser) and record no misses. The
# absolute floor keeps small venues working: a 3-show calendar that really drops
# 2 shows still records normally.
MASS_DISAPPEARANCE_MIN = 5

# Max stored headliner length. Sourced from the Event.headliner column
# (String(300)) so the model stays the single source of truth for the width —
# the upsert clamps to this before writing.
HEADLINER_MAX_LEN = Event.__table__.c.headliner.type.length


# --- ScrapeManager ---

class ScrapeManager:
    """Orchestrates scraping for all venues."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _get_scraper(self, venue: Venue) -> Optional[BaseScraper]:
        """Instantiate the correct scraper for a venue."""
        if venue.scraper_type == "ticketmaster":
            if not venue.ticketmaster_venue_id:
                logger.warning(f"No TM venue ID for {venue.slug}")
                return None
            return TicketmasterScraper(
                venue_slug=venue.slug,
                venue_tm_id=venue.ticketmaster_venue_id,
                api_key=settings.TICKETMASTER_API_KEY,
                config=venue.scraper_config,
            )
        # All other scraper types dispatch through the canonical registry in
        # identity.py (which also carries the per-scraper identity verdicts);
        # classes are resolved lazily so scraper deps load only when used.
        cls = scraper_class(venue.scraper_type)
        if cls is None:
            logger.warning(f"Unknown scraper type: {venue.scraper_type}")
            return None
        return cls(venue.slug, venue.scraper_config)

    # --- Per-venue scrape logic ---

    async def scrape_venue(self, venue: Venue) -> dict:
        """Scrape a single venue and upsert events."""
        # Refresh venue before accessing any attributes. If a previous scrape_venue call
        # ended in a rollback, all ORM objects in the session are expired; accessing an
        # expired attribute on an AsyncSession triggers a sync lazy-load → greenlet error.
        await self.session.refresh(venue)
        venue_slug = venue.slug
        venue_id = venue.id
        scraper_type = venue.scraper_type

        # Create a ScrapeLog row up front so we have a record even if the scrape crashes.
        log = ScrapeLog(
            venue_id=venue_id,
            scraper_type=scraper_type,
            started_at=datetime.utcnow(),
        )
        try:
            self.session.add(log)
            await self.session.flush()

            scraper = self._get_scraper(venue)
            if not scraper:
                raise ValueError(f"No scraper available for {venue_slug}")

            scraped_events = await scraper.scrape()

            # Serialize the write phase (upsert + snapshot diff + commit) per venue.
            # Overlapping scrapes are real — the startup scrape task, APScheduler
            # crons, POST /api/scrape, and during a rolling deploy a whole second
            # process — and unserialized they can deadlock on unordered row updates
            # or strand each other's miss-state writes mid-diff. A transaction-scoped
            # advisory lock covers coroutines and processes alike and releases itself
            # at commit/rollback, even if the connection dies. hashtext(database)
            # partitions the cluster-global lock space so parallel test databases
            # (pytest-xdist) never contend with each other. The slow network fetch
            # above deliberately stays outside the lock.
            await self.session.execute(
                select(func.pg_advisory_xact_lock(func.hashtext(func.current_database()), venue_id))
            )
            created, updated = await self._upsert_events(venue_id, scraped_events)
            # Same transaction as the upsert: a crash below rolls everything back,
            # so misses can never be recorded for a scrape that never logged success.
            tombstoned, relisted = await self._apply_snapshot_diff(venue_id, scraped_events)

            log.status = "success"
            log.events_found = len(scraped_events)
            log.events_created = created
            log.events_updated = updated
            log.finished_at = datetime.utcnow()
            log.duration_seconds = (log.finished_at - log.started_at).total_seconds()
            await self.session.commit()

            logger.info(
                f"[{venue_slug}] Scrape complete: {len(scraped_events)} found, "
                f"{created} created, {updated} updated, "
                f"{tombstoned} tombstoned, {relisted} relisted"
            )
            return {
                "venue": venue_slug,
                "status": "success",
                "found": len(scraped_events),
                "created": created,
                "updated": updated,
                "tombstoned": tombstoned,
                "relisted": relisted,
            }

        except Exception as e:
            logger.error(f"[{venue_slug}] Scrape failed: {e}")
            try:
                # Roll back the failed transaction before writing the error log,
                # otherwise the commit below will also fail.
                await self.session.rollback()
                log.status = "failed"
                log.error_message = str(e)[:2000]  # cap length to fit DB column
                log.finished_at = datetime.utcnow()
                log.duration_seconds = (log.finished_at - log.started_at).total_seconds()
                self.session.add(log)
                await self.session.commit()
            except Exception as log_err:
                logger.warning(f"[{venue_slug}] Could not write error log: {log_err}")
            return {"venue": venue_slug, "status": "failed", "error": str(e)}

    # --- Upsert helpers ---

    async def _upsert_events(self, venue_id: int, scraped_events: list[ScrapedEvent]) -> tuple[int, int]:
        """Upsert events, reconciling by identity precedence (issue #8).

        Each scraped event matches an existing row per-venue by external_id,
        then normalized source_url (audit-TRUSTED scrapers only), then content
        hash — so renames and reschedules become in-place updates instead of a
        new row plus a lingering orphan. Returns (created, updated) counts.
        """
        if not scraped_events:
            return 0, 0

        venue = await self.session.get(Venue, venue_id)
        verdict = url_identity_verdict(venue.scraper_type) if venue else UrlIdentityVerdict.HASH_FALLBACK

        # Precompute per event: ScrapedEvent.hash is a sha256-per-access property
        # and URL normalization parses the query string — both are consulted
        # several times below.
        prepared = [(se, se.hash, normalize_source_url(se.source_url)) for se in scraped_events]

        # --- In-batch dedup -------------------------------------------------
        # Group by identity (derive_source_key is the single source of truth for
        # tier precedence); within a group, the same date means a true duplicate
        # (featured section + main listing, or an old/new time pair) and collapses
        # to the first occurrence. DIFFERENT dates mean distinct events sharing an
        # identity key (e.g. a recurring series whose occurrences share one URL) —
        # trusting that key would let one occurrence overwrite the other, so every
        # event in such a group demotes to hash identity for this batch.
        groups: dict[str, dict] = {}
        for se, h, norm in prepared:
            key = derive_source_key(se.external_id, norm, h, verdict)
            groups.setdefault(key, {}).setdefault(se.date, (se, h, norm))

        # batch: source_key -> (event, hash, normalized_url, demoted)
        batch: dict[str, tuple[ScrapedEvent, str, Optional[str], bool]] = {}
        for key, by_date in groups.items():
            if len(by_date) > 1:
                for se, h, norm in by_date.values():
                    batch.setdefault(derive_source_key(None, None, h, verdict), (se, h, norm, True))
            else:
                se, h, norm = next(iter(by_date.values()))
                batch.setdefault(key, (se, h, norm, False))

        # --- Candidate fetch: one venue-scoped query across all three tiers ---
        # Venue scoping is load-bearing: external_id/URL uniqueness is only assumed
        # within a venue (VenuePilot ids are small integers that collide across
        # venues). The hash used to embed venue_slug and make this implicit.
        ext_ids = {se.external_id for se, _, _, _ in batch.values() if se.external_id}
        norm_urls = {
            norm for _, _, norm, _ in batch.values() if norm
        } if verdict is UrlIdentityVerdict.TRUSTED else set()
        hashes = {h for _, h, _, _ in batch.values()}

        conditions = [Event.hash.in_(hashes)]
        if ext_ids:
            conditions.append(Event.external_id.in_(ext_ids))
        if norm_urls:
            conditions.append(Event.normalized_source_url.in_(norm_urls))
        result = await self.session.execute(
            select(Event).where(Event.venue_id == venue_id, or_(*conditions)).order_by(Event.id)
        )
        by_ext: dict[str, Event] = {}
        by_url: dict[str, Event] = {}
        by_hash: dict[str, Event] = {}
        by_hash_keyed: dict[str, Event] = {}
        url_counts: dict[str, int] = {}
        for row in result.scalars().all():
            # setdefault + id ordering: when duplicate rows share a key (legal for
            # hash, and for URL until the backfill merges them), the oldest row wins
            # deterministically.
            if row.external_id:
                by_ext.setdefault(row.external_id, row)
            if row.normalized_source_url:
                url_counts[row.normalized_source_url] = url_counts.get(row.normalized_source_url, 0) + 1
                by_url.setdefault(row.normalized_source_url, row)
            by_hash.setdefault(row.hash, row)
            if row.source_key == f"hash:{row.hash}":
                by_hash_keyed.setdefault(row.hash, row)

        # A URL shared by MULTIPLE stored rows is not identity — the cross-batch
        # mirror of the in-batch demotion rule. Without this, a later scrape
        # listing only one occurrence would url-match the OLDEST same-URL row and
        # rewrite the wrong occurrence in place. Demote affected batch entries to
        # hash identity, and never url-match against an ambiguous URL.
        ambiguous_urls = {u for u, c in url_counts.items() if c > 1}
        if ambiguous_urls:
            rebatched: dict[str, tuple[ScrapedEvent, str, Optional[str], bool]] = {}
            for key, (se, h, norm, demoted) in batch.items():
                if key.startswith("url:") and norm in ambiguous_urls:
                    rebatched.setdefault(derive_source_key(None, None, h, verdict), (se, h, norm, True))
                else:
                    rebatched.setdefault(key, (se, h, norm, demoted))
            batch = rebatched

        def hash_match(h: str) -> Optional[Event]:
            """Hash-tier lookup, preferring the row that already HOLDS hash:<h>.

            Duplicate hashes are legal; if the oldest hash-matching row were
            chosen while a different row holds the hash:<h> source_key, forcing
            that key onto the older row would collide under the
            (venue_id, source_key) unique index and fail the venue's scrape.
            """
            return by_hash_keyed.get(h) or by_hash.get(h)

        def match(se: ScrapedEvent, h: str, norm: Optional[str], key: str) -> Optional[Event]:
            """Find the existing row for a scraped event, falling through tiers.

            Fall-through is what makes tier transitions seamless: an event that
            used to be keyed by URL and now carries an external_id still finds
            its row via the URL (or hash) column. Demoted events (hash key
            despite having a URL) match by hash ONLY — their URL is ambiguous,
            and URL-matching would pile them onto one row.
            """
            if key.startswith("ext:"):
                row = by_ext.get(se.external_id)
                if row is None and norm and norm not in ambiguous_urls:
                    row = by_url.get(norm)
                return row or hash_match(h)
            if key.startswith("url:"):
                return by_url.get(norm) or hash_match(h)
            return hash_match(h)

        created = 0
        updated = 0
        claimed: set[int] = set()

        for key, (se, h, norm, demoted) in batch.items():
            # Best-effort clean performer (issue #18). A scraper-supplied structured
            # performer (schema.org Event.performer, Ticketmaster attractions) is
            # ALREADY authoritative and clean, so it is trusted VERBATIM — only
            # whitespace-normalized and width-clamped. Running the billing-string
            # heuristic over it would mangle a real band whose name happens to match
            # a strip/null pattern (e.g. "Karaoke From Hell", or a name containing
            # "feat."/"Presents:"). The heuristic applies ONLY to the raw name
            # fallback, where there is no structured performer to trust.
            structured = (se.headliner or "").strip()
            if structured:
                headliner = " ".join(structured.split())
            else:
                headliner = extract_headliner(se.name)
            if headliner:
                headliner = headliner[:HEADLINER_MAX_LEN]

            existing = match(se, h, norm, key)
            if existing is not None and existing.id in claimed:
                # Two batch identities resolved to one stored row (e.g. an ext-keyed
                # and a url-keyed listing of the same event). First claim wins;
                # inserting the second would duplicate the event.
                continue

            if existing is not None:
                claimed.add(existing.id)
                # Identity fields update in place — that's the point of issue #8:
                # a rename or reschedule is the same event with new values.
                existing.name = se.name
                existing.date = se.date
                existing.hash = h
                existing.price_min = se.price_min
                existing.price_max = se.price_max
                existing.status = se.status
                # headliner is derived data — it tracks the current name/performer
                # deterministically (assigned unconditionally, unlike the merge-
                # preserved fields below), so a rename to a non-performance billing
                # correctly nulls it instead of leaving a stale artist behind.
                existing.headliner = headliner
                # Prefer the freshly scraped value but fall back to whatever we already
                # have stored so we don't accidentally blank out previously-good data.
                existing.artist = se.artist or existing.artist
                existing.external_id = se.external_id or existing.external_id
                existing.source_url = se.source_url or existing.source_url
                existing.image_url = se.image_url or existing.image_url
                existing.ticket_url = se.ticket_url or existing.ticket_url
                existing.doors_time = se.doors_time or existing.doors_time
                existing.show_time = se.show_time or existing.show_time
                existing.support_artists = se.support_artists or existing.support_artists
                existing.genre = se.genre or existing.genre
                existing.subgenre = se.subgenre or existing.subgenre
                existing.age_restriction = se.age_restriction or existing.age_restriction
                existing.description = se.description or existing.description
                existing.normalized_source_url = normalize_source_url(existing.source_url)
                # source_key recomputes from the MERGED values so a row that keeps
                # its stored external_id keeps its ext: key even when one scrape
                # omits the id. Demoted events force the hash tier — their URL is
                # shared, and a url: key here would collide with the other row
                # holding it under the (venue_id, source_key) unique index. The
                # same ambiguity guard applies to the merged URL.
                if demoted:
                    existing.source_key = derive_source_key(None, None, h, verdict)
                else:
                    merged_norm = existing.normalized_source_url
                    if merged_norm in ambiguous_urls:
                        merged_norm = None
                    existing.source_key = derive_source_key(existing.external_id, merged_norm, h, verdict)
                # updated_at is NOT stamped here: the column's onupdate fires only when
                # an assignment above actually changed a value, which keeps updated_at
                # meaningful as a "this row's data changed" signal for API clients.
                # Count as updated only when a value really changed, so ScrapeLog's
                # events_updated matches the rows the upsert modified (tombstone and
                # relist updates from the snapshot diff are reported separately).
                if self.session.is_modified(existing):
                    updated += 1
            else:
                event = Event(
                    external_id=se.external_id,
                    venue_id=venue_id,
                    name=se.name,
                    artist=se.artist,
                    headliner=headliner,
                    support_artists=se.support_artists,
                    date=se.date,
                    doors_time=se.doors_time,
                    show_time=se.show_time,
                    ticket_url=se.ticket_url,
                    price_min=se.price_min,
                    price_max=se.price_max,
                    image_url=se.image_url,
                    genre=se.genre,
                    subgenre=se.subgenre,
                    status=se.status,
                    age_restriction=se.age_restriction,
                    description=se.description,
                    source=se.source,
                    source_url=se.source_url,
                    normalized_source_url=norm,
                    hash=h,
                    # The batch key IS the identity: ext:/url: for stable tiers,
                    # hash: for hash-tier and demoted events.
                    source_key=key,
                )
                self.session.add(event)
                created += 1

        await self.session.flush()
        return created, updated

    # --- Vanished-event snapshot diff ---

    async def _apply_snapshot_diff(self, venue_id: int, scraped_events: list[ScrapedEvent]) -> tuple[int, int]:
        """Diff a successful scrape's snapshot against the venue's stored events.

        Treats the scrape as a full snapshot of that venue's listing window: stored
        events absent from it accrue misses (at most one per Triangle calendar day),
        and a second miss on a later day within MISS_STREAK_MAX_GAP_DAYS stamps the
        soft tombstone (Event.removed_at). Absence evidence is heavily guarded —
        empty snapshots, mass disappearances, and anything at or past the horizon
        count for nothing — while presence evidence (an event appearing) is always
        trusted: it resets the miss streak and clears the tombstone.

        Scoping invariant: this method matches the venue's events by hash, which
        embeds the venue *slug* (ScrapedEvent.hash), while rows carry a venue *id* —
        the diff is only correct while slug and id agree, which every code path
        preserves (venues are seeded by slug and deleted with their events; nothing
        renames a slug in place).

        Returns (tombstoned, relisted) counts for the scrape's audit trail.
        """
        # A zero-event snapshot counts for nothing: it almost certainly means a
        # broken scraper, not a mass cancellation (the manager logs 'success' for
        # zero-event scrapes, so this gate cannot live on ScrapeLog status).
        if not scraped_events:
            return 0, 0

        today = today_in_triangle()
        snapshot_hashes = {se.hash for se in scraped_events}
        # Horizon guard: only events strictly before this scrape's max seen date are
        # candidates. Exclusive on purpose: item-capped listings can cut mid-date,
        # so the boundary date itself is unvouched; a truncated page also lowers
        # the horizon. Per-scrape by design.
        horizon = max(se.date for se in scraped_events)

        # Presence processing runs unconditionally — an appearance is reliable
        # evidence even when absence (below) is not. One fetch drives both the
        # miss-streak reset and the tombstone clear. Clearing removed_at goes
        # through the ORM row so its onupdate stamps updated_at: reappearance is
        # a client-visible change, unlike the miss bookkeeping.
        appeared_result = await self.session.execute(
            select(Event).where(Event.venue_id == venue_id, Event.hash.in_(snapshot_hashes))
        )
        appeared = appeared_result.scalars().all()
        relisted = 0
        if appeared:
            await self.session.execute(
                delete(EventMissState).where(
                    EventMissState.event_id.in_([e.id for e in appeared])
                )
            )
            for event in appeared:
                if event.removed_at is not None:
                    event.removed_at = None
                    relisted += 1

        # One fetch of the venue's LIVE in-window events, partitioned in Python, so
        # the mass guard's numerator and denominator share a single predicate by
        # construction. Tombstoned rows are deliberately excluded: their absence is
        # already explained, so they are neither disappearance evidence (a tombstone
        # backlog would trip the mass guard on every scrape and suppress all new
        # detections) nor in need of further miss upkeep. The exclusive horizon means
        # the venue's latest-dated stored event is structurally unobservable — the
        # accepted price of never trusting the possibly-truncated boundary date.
        in_window_result = await self.session.execute(
            select(Event).where(
                Event.venue_id == venue_id,
                Event.date >= today,
                Event.date < horizon,
                Event.removed_at.is_(None),
            )
        )
        in_window = in_window_result.scalars().all()
        missing = [e for e in in_window if e.hash not in snapshot_hashes]
        if not missing:
            return 0, relisted

        # Mass-disappearance guard: when most of the venue's live in-window calendar
        # vanishes in one scrape, the page is degraded (bot challenge, half-rendered
        # listing, partial parse failure), not mass-delisted — record nothing.
        # Deliberately fail-safe toward not tombstoning: a venue that genuinely
        # delists most of its calendar at once keeps the signal suppressed until the
        # snapshot again covers the majority of what we have stored.
        if len(missing) >= MASS_DISAPPEARANCE_MIN and 2 * len(missing) > len(in_window):
            logger.warning(
                f"[venue {venue_id}] {len(missing)} of {len(in_window)} live in-window "
                f"events missing from a {len(scraped_events)}-event snapshot — "
                f"treating as scraper breakage, no misses recorded"
            )
            return 0, relisted

        states_result = await self.session.execute(
            select(EventMissState).where(EventMissState.event_id.in_([e.id for e in missing]))
        )
        states = {s.event_id: s for s in states_result.scalars()}

        tombstoned = 0
        first_misses: list[int] = []
        for event in missing:
            state = states.get(event.id)
            if state is None:
                first_misses.append(event.id)
            elif state.last_miss_date >= today:
                # At most one miss per Triangle calendar day: indie venues scrape
                # 3x/day, and one degraded-but-nonzero day must never tombstone.
                # >= (not ==) so a backward clock step defers, never accelerates.
                continue
            elif (today - state.last_miss_date).days > MISS_STREAK_MAX_GAP_DAYS:
                # Stale streak: the event went unobserved too long for the old miss
                # to corroborate this one — restart, today is miss #1 again.
                state.last_miss_date = today
            else:
                # A second miss on a later day: the venue delisted this event.
                # `missing` holds only live rows, so this never re-stamps a tombstone
                # (updated_at must not churn), and status is never touched —
                # removed_at records "the venue no longer advertises this", no more.
                state.last_miss_date = today
                event.removed_at = datetime.utcnow()
                tombstoned += 1

        if first_misses:
            # ON CONFLICT DO NOTHING: belt-and-braces under the per-venue advisory
            # lock in scrape_venue — even if a same-venue writer ever slips past the
            # serialization, a duplicate first-miss must not blow up the transaction.
            await self.session.execute(
                pg_insert(EventMissState)
                .values([{"event_id": eid, "last_miss_date": today} for eid in first_misses])
                .on_conflict_do_nothing()
            )

        return tombstoned, relisted

    # --- Bulk scrape entry points ---

    async def scrape_all(self, scraper_types: Optional[list[str]] = None) -> list[dict]:
        """Scrape all venues (or those matching given scraper_types)."""
        query = select(Venue)
        if scraper_types:
            query = query.where(Venue.scraper_type.in_(scraper_types))
        result = await self.session.execute(query)
        venues = result.scalars().all()

        results = []
        # Venues are scraped sequentially to keep the session state simple and avoid
        # concurrent writes on the same async session (AsyncSession is not thread-safe).
        for venue in venues:
            r = await self.scrape_venue(venue)
            results.append(r)

        return results

    async def scrape_ticketmaster(self) -> list[dict]:
        """Scrape only Ticketmaster venues."""
        return await self.scrape_all(scraper_types=["ticketmaster"])

    async def scrape_indie(self) -> list[dict]:
        """Scrape only non-Ticketmaster venues."""
        query = select(Venue).where(Venue.scraper_type != "ticketmaster")
        result = await self.session.execute(query)
        venues = result.scalars().all()

        results = []
        for venue in venues:
            r = await self.scrape_venue(venue)
            results.append(r)
        return results
