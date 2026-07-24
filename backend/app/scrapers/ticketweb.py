"""
TicketWeb scraper — parses events from schema.org JSON-LD embedded on a
TicketWeb venue's public event-listing page.

Role: One of many venue scrapers invoked by scrapers/manager.py during each
scrape cycle. TicketWeb (a Live Nation subsidiary) backs a large share of
Seattle's mid-size indie clubs (Tractor Tavern, Chop Suey, Clock-Out Lounge,
El Corazón) plus the plurality of The Crocodile's listings, per the
SeattleShows.net initiative (issue #67). Parameterized by venue token from
scraper_config so any TicketWeb venue can be onboarded by config alone; the
Seattle venue seed entries themselves are out of scope here (see #71).
Requires: app.scrapers.base (BaseScraper, ScrapedEvent) for the shared
fetch_soup HTTP path and extract_schema_image() image normalization; venue
config must include ``ticketweb_slug`` and ``ticketweb_id`` — TicketWeb venue
pages live at ticketweb.com/venue/<slug>/<id> (mirrors how the ticketmaster
scraper keys on ticketmaster_venue_id).
"""
# --- Imports ---
import json
import logging
import re
from datetime import date, datetime
from html import unescape
from typing import Optional

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---

logger = logging.getLogger(__name__)

# Trailing numeric event id in a TicketWeb event URL, e.g.
# https://www.ticketweb.com/event/futurebirds-far-out-tractor-tickets/14751933
# -> "14751933". Live-fetch confirmed (2026-07-21, Tractor venue page) that a
# two-night stand (same headliner, two dates) carries two distinct ids here.
_EVENT_ID_RE = re.compile(r"/(\d+)/?(?:\?.*)?$")


# --- Scraper class ---

class TicketWebScraper(BaseScraper):
    """Scrape events from a TicketWeb venue page via embedded schema.org JSON-LD.

    Config: {"ticketweb_slug": "tractor-seattle-wa", "ticketweb_id": "18807"}
    """

    # Audit (issue #67): source_url is the per-event ticket page. A live fetch
    # of Tractor's venue page (2026-07-21, via Wayback Machine after the direct
    # host bot-blocked the scraper) showed occurrence-uniqueness — a two-night
    # stand carried two distinct URLs — but rename/reschedule stability across
    # TicketWeb's URL scheme is unverified from a single snapshot. Identity
    # instead comes from external_id (the numeric TicketWeb event id parsed
    # from the URL), so this stays HASH_FALLBACK, matching ticketmaster and
    # venuepilot rather than claiming an unaudited TRUSTED verdict.
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the TicketWeb venue page and extract all upcoming events."""
        slug = self.config.get("ticketweb_slug")
        venue_id = self.config.get("ticketweb_id")
        if not slug or not venue_id:
            raise ValueError(f"No ticketweb_slug/ticketweb_id configured for {self.venue_slug}")

        url = f"https://www.ticketweb.com/venue/{slug}/{venue_id}"
        soup = await self.fetch_soup(url)

        events = []
        # TicketWeb embeds event data as one or more <script type="application/ld+json">
        # blocks; the venue page carries a bare array of MusicEvent objects plus a
        # trailing EventVenue block describing the venue itself (not an event).
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") not in ("Event", "MusicEvent"):
                    continue
                parsed = self._parse_event(item)
                if parsed:
                    events.append(parsed)

        logger.info(f"[TicketWeb] Found {len(events)} events for {self.venue_slug}")
        return events

    def _parse_event(self, data: dict) -> Optional[ScrapedEvent]:
        """Parse a single schema.org MusicEvent dict into a ScrapedEvent, or None if invalid."""
        try:
            name = unescape(data.get("name", "") or "").strip()
            if not name:
                return None

            start = data.get("startDate", "")
            if not start:
                return None
            try:
                if "T" in start:
                    dt = datetime.fromisoformat(start)
                    event_date = dt.date()
                    show_time = dt.time()
                else:
                    event_date = date.fromisoformat(start[:10])
                    show_time = None
            except ValueError:
                return None

            # The venue page is a live listing; a past-dated entry would only
            # appear from a stale/misconfigured feed, not a real upcoming show.
            if event_date < date.today():
                return None

            ticket_url = data.get("url") or (data.get("offers") or {}).get("url")

            # performer is a list of {"name": ...} dicts; TicketWeb lists the
            # headliner first, support acts after (mirrors ticketmaster's
            # attractions[] convention).
            headliner = None
            support: list[str] = []
            performers = data.get("performer")
            if isinstance(performers, dict):
                performers = [performers]
            if isinstance(performers, list):
                names = [
                    unescape(p.get("name", "") or "").strip()
                    for p in performers
                    if isinstance(p, dict) and p.get("name")
                ]
                names = [n for n in names if n]
                if names:
                    headliner = names[0]
                    support = names[1:]

            external_id = None
            if ticket_url:
                match = _EVENT_ID_RE.search(ticket_url)
                if match:
                    external_id = match.group(1)

            # schema.org's `image` is polymorphic (bare URL, list, ImageObject);
            # BaseScraper.extract_schema_image normalizes it to a URL or None.
            image_url = self.extract_schema_image(data.get("image"))

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="ticketweb",
                external_id=external_id,
                artist=headliner or name,
                headliner=headliner,
                support_artists=support,
                show_time=show_time,
                ticket_url=ticket_url,
                image_url=image_url,
                source_url=ticket_url,
            )
        except Exception as e:
            logger.warning(f"[TicketWeb] Failed to parse event: {e}")
            return None
