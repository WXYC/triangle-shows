"""
Scrape orchestrator: runs all venue scrapers, deduplicates results by hash, and
upserts events + scrape logs into the database.

Role: Triggered by POST /api/scrape (called by the scheduler every 6 hours or
by Cloud Scheduler). Sits between the individual scrapers and the database —
it owns the fan-out, error isolation, and upsert logic.
Requires: TICKETMASTER_API_KEY (via app.config.settings), async PostgreSQL
session, and all scraper modules in app.scrapers/.
"""

# --- Imports ---
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.market_time import today_in_triangle
from app.models import Venue, Event, EventMissState, ScrapeLog
from app.scrapers.base import BaseScraper, ScrapedEvent
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
        # Remaining scraper types are imported lazily to avoid circular imports
        # and to keep startup time fast when only a subset of scrapers are used.
        elif venue.scraper_type == "rhp_events":
            from app.scrapers.rhp_events import RHPEventsScraper
            return RHPEventsScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "tribe_events":
            from app.scrapers.tribe_events import TribeEventsScraper
            return TribeEventsScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "squarespace":
            from app.scrapers.squarespace import SquarespaceScraper
            return SquarespaceScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "eventprime":
            from app.scrapers.eventprime import EventPrimeScraper
            return EventPrimeScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "motorco":
            from app.scrapers.motorco import MotorcoScraper
            return MotorcoScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "carolina_theatre":
            from app.scrapers.carolina_theatre import CarolinaTheatreScraper
            return CarolinaTheatreScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "venuepilot":
            from app.scrapers.venuepilot import VenuePilotScraper
            return VenuePilotScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "koka_booth":
            from app.scrapers.koka_booth import KokaBoothScraper
            return KokaBoothScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "mec":
            from app.scrapers.mec import MECScraper
            return MECScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "webflow_cms":
            from app.scrapers.webflow_cms import WebflowCMSScraper
            return WebflowCMSScraper(venue.slug, venue.scraper_config)
        elif venue.scraper_type == "tickpick_organizer":
            from app.scrapers.tickpick_organizer import TickPickOrganizerScraper
            return TickPickOrganizerScraper(venue.slug, venue.scraper_config)
        else:
            logger.warning(f"Unknown scraper type: {venue.scraper_type}")
            return None

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
        """Upsert events using hash-based dedup. Returns (created, updated) counts."""
        if not scraped_events:
            return 0, 0

        # Deduplicate scraped events by hash (sites sometimes list the same event
        # twice — e.g. a featured section + main listing — which would cause a
        # UniqueViolationError when both end up in the same INSERT flush batch.
        seen: dict[str, ScrapedEvent] = {}
        for se in scraped_events:
            seen.setdefault(se.hash, se)
        scraped_events = list(seen.values())

        # Fetch all matching existing events in one query instead of one per event.
        hashes = [se.hash for se in scraped_events]
        result = await self.session.execute(
            select(Event).where(Event.hash.in_(hashes))
        )
        existing_by_hash = {e.hash: e for e in result.scalars().all()}

        created = 0
        updated = 0

        for se in scraped_events:
            existing = existing_by_hash.get(se.hash)

            if existing:
                # Update mutable fields — identity fields (name, date, venue) are
                # baked into the hash so they never change for a given event row.
                existing.price_min = se.price_min
                existing.price_max = se.price_max
                existing.status = se.status
                # Prefer the freshly scraped value but fall back to whatever we already
                # have stored so we don't accidentally blank out previously-good data.
                existing.image_url = se.image_url or existing.image_url
                existing.ticket_url = se.ticket_url or existing.ticket_url
                existing.doors_time = se.doors_time or existing.doors_time
                existing.show_time = se.show_time or existing.show_time
                existing.support_artists = se.support_artists or existing.support_artists
                existing.genre = se.genre or existing.genre
                existing.subgenre = se.subgenre or existing.subgenre
                existing.age_restriction = se.age_restriction or existing.age_restriction
                existing.description = se.description or existing.description
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
                    hash=se.hash,
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

        missing_result = await self.session.execute(
            select(Event).where(
                Event.venue_id == venue_id,
                Event.date >= today,
                Event.date < horizon,
                Event.hash.not_in(snapshot_hashes),
            )
        )
        missing = missing_result.scalars().all()
        if not missing:
            await self.session.flush()
            return 0, relisted

        # Mass-disappearance guard: when most of the venue's in-window calendar
        # vanishes in one scrape, the page is degraded (bot challenge, half-rendered
        # listing, partial parse failure), not mass-delisted — record nothing.
        # Deliberately fail-safe toward not tombstoning: a venue that genuinely
        # delists most of its calendar will keep the signal suppressed until the
        # snapshot again covers the majority of what we have stored.
        in_window_total = (
            await self.session.execute(
                select(func.count())
                .select_from(Event)
                .where(Event.venue_id == venue_id, Event.date >= today, Event.date < horizon)
            )
        ).scalar_one()
        if len(missing) >= MASS_DISAPPEARANCE_MIN and 2 * len(missing) > in_window_total:
            logger.warning(
                f"[venue {venue_id}] {len(missing)} of {in_window_total} in-window "
                f"events missing from a {len(scraped_events)}-event snapshot — "
                f"treating as scraper breakage, no misses recorded"
            )
            await self.session.flush()
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
                # Never re-stamp (updated_at must not churn), never touch status —
                # removed_at records "the venue no longer advertises this", no more.
                state.last_miss_date = today
                if event.removed_at is None:
                    event.removed_at = datetime.utcnow()
                    tombstoned += 1

        if first_misses:
            # ON CONFLICT DO NOTHING: a concurrent scrape of the same venue (startup
            # scrape overlapping a scheduled run) may have inserted the same row; the
            # loser must not blow up its whole transaction on the primary key.
            await self.session.execute(
                pg_insert(EventMissState)
                .values([{"event_id": eid, "last_miss_date": today} for eid in first_misses])
                .on_conflict_do_nothing()
            )

        await self.session.flush()
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
