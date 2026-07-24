"""
Scraper for venues that sell tickets through Eventbrite.

Role: Instantiated and called by the scrape manager (scrapers/manager.py) during
each scrape cycle. Motivated by Substation and The Vera Project (Seattle); the
scraper is venue-agnostic, keyed by an Eventbrite organizer id from venue config.
Requires: httpx, beautifulsoup4/lxml; venue config must supply "organizer_id"
(the slug from the organizer's own page, e.g. "substation-18831550522" out of
https://www.eventbrite.com/o/substation-18831550522 — the bare numeric id also
resolves, but the full slug is what the organizer's own page links use).

Two-hop fetch, mirroring the mec.py pattern:
  1. Fetch the organizer's listing page purely to discover each event's detail
     URL. Eventbrite's listing page is a Next.js app that server-renders event
     data only into a `<script id="__NEXT_DATA__">` JSON blob (confirmed live
     2026-07-24: no schema.org JSON-LD and no plain `<a href>` event links on
     that page) — so this step reads `props.pageProps.upcomingEvents[].url`
     and nothing else from it. A shift in that internal prop shape degrades to
     zero discovered events (logged), not corrupted event data.
  2. Fetch each per-event detail page and parse its schema.org JSON-LD
     `<script type="application/ld+json">` Event block (issue #24 pattern) —
     that is Eventbrite's stable, documented per-event contract, confirmed live
     against multiple Substation events on 2026-07-24.
"""

# --- Imports ---
import json
import logging
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---
logger = logging.getLogger(__name__)


# --- Scraper class ---

class EventbriteScraper(BaseScraper):
    """Scrape events from an Eventbrite organizer's public listing page.

    Used by: Substation, The Vera Project (Seattle)
    Config: {"organizer_id": "substation-18831550522"}
    """

    # Audit (issue #8): source_url is the event's own per-event Eventbrite page
    # (https://www.eventbrite.com/e/{title-slug}-tickets-{id}). The title-derived
    # slug portion is not confirmed rename-stable — Eventbrite may regenerate it
    # when an organizer edits the event name — so the URL text cannot anchor
    # identity (same reasoning as squarespace.py). The trailing numeric segment
    # IS a stable per-event id, extracted below as external_id, which
    # reconciliation prefers over source_url regardless of this verdict.
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    # Eventbrite event URLs end in "-tickets-{numeric id}" (or just "-{id}");
    # the trailing digit run is the durable per-event id.
    _EVENT_ID_RE = re.compile(r"-(\d+)/?(?:[?#].*)?$")

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the organizer listing, then each linked event's JSON-LD."""
        organizer_id = self.config.get("organizer_id", "")
        if not organizer_id:
            raise ValueError(f"No organizer_id configured for {self.venue_slug}")

        listing_url = f"https://www.eventbrite.com/o/{organizer_id}"
        events: list[ScrapedEvent] = []

        async with self.http_client() as client:
            soup = await self.fetch_soup(listing_url, client=client)
            detail_urls = self._extract_event_urls(soup)

            for detail_url in detail_urls:
                try:
                    detail_soup = await self.fetch_soup(detail_url, client=client)
                except Exception as e:
                    logger.warning(f"[Eventbrite] Failed to fetch {detail_url}: {e}")
                    continue
                parsed = self._parse_event_page(detail_soup)
                if parsed:
                    events.append(parsed)

        logger.info(f"[Eventbrite] Found {len(events)} events for {self.venue_slug}")
        return events

    def _extract_event_urls(self, soup: BeautifulSoup) -> list[str]:
        """Pull each event's detail-page URL out of the listing's __NEXT_DATA__ blob.

        Link discovery only — no event field is read from this blob, so a shift
        in Eventbrite's internal Next.js prop shape yields zero events (logged)
        rather than corrupted data.
        """
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            logger.warning(f"[Eventbrite] No __NEXT_DATA__ blob for {self.venue_slug}")
            return []
        try:
            data = json.loads(script.get_text())
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[Eventbrite] Malformed __NEXT_DATA__ for {self.venue_slug}")
            return []

        try:
            upcoming = data["props"]["pageProps"]["upcomingEvents"]
        except (KeyError, TypeError):
            upcoming = []
        if not isinstance(upcoming, list):
            return []

        urls: list[str] = []
        for item in upcoming:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str) and url and url not in urls:
                urls.append(url)
        return urls

    def _parse_event_page(self, soup: BeautifulSoup) -> Optional[ScrapedEvent]:
        """Find the schema.org Event block among a detail page's JSON-LD scripts."""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    if "Event" not in item_type and "MusicEvent" not in item_type:
                        continue
                elif item_type not in ("Event", "MusicEvent"):
                    continue
                return self._parse_jsonld_event(item)
        return None

    def _parse_jsonld_event(self, data: dict) -> Optional[ScrapedEvent]:
        """Convert a single schema.org Event dict (an Eventbrite detail page's own
        JSON-LD) into a ScrapedEvent, or return None if invalid."""
        try:
            name = (data.get("name") or "").strip()
            if not name:
                return None

            start = data.get("startDate") or ""
            if not start:
                return None
            try:
                # Eventbrite emits a full offset-aware ISO datetime in the
                # venue's local time (e.g. "2026-07-24T22:00:00-07:00"); take
                # the local date/time components as-is, same as mec/tickpick.
                dt = datetime.fromisoformat(start)
            except ValueError:
                return None
            event_date = dt.date()
            show_time = dt.time()

            url = data.get("url")
            url = url if isinstance(url, str) else None
            external_id = self._extract_external_id(url)

            # Performers — schema.org Event.performer, first is the headliner.
            performers = data.get("performer", [])
            if isinstance(performers, dict):
                performers = [performers]
            elif not isinstance(performers, list):
                performers = []
            performer_names = [
                p.get("name") for p in performers if isinstance(p, dict) and p.get("name")
            ]
            headliner = performer_names[0] if performer_names else None
            support = performer_names[1:]
            artist = headliner or name

            # Offers / price — Eventbrite emits a single-element AggregateOffer list.
            price_min = None
            price_max = None
            offers = data.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                raw_min = offers.get("lowPrice") or offers.get("price")
                raw_max = offers.get("highPrice") or raw_min
                price_min = self._coerce_price(raw_min)
                price_max = self._coerce_price(raw_max)

            # Status — derive from schema eventStatus or a free price.
            event_status = data.get("eventStatus") or ""
            if "Cancelled" in event_status:
                status = "cancelled"
            elif price_min == 0:
                status = "free"
            else:
                status = "on_sale"

            # schema.org's `image` is polymorphic; BaseScraper.extract_schema_image
            # normalizes it. Eventbrite emits a bare URL string.
            image_url = self.extract_schema_image(data.get("image"))

            description = data.get("description") or None

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="eventbrite",
                artist=artist,
                headliner=headliner,
                support_artists=support,
                show_time=show_time,
                ticket_url=url,
                price_min=price_min,
                price_max=price_max,
                image_url=image_url,
                status=status,
                description=description,
                external_id=external_id,
                source_url=url,
            )
        except Exception as e:
            logger.warning(f"[Eventbrite] Failed to parse event: {e}")
            return None

    def _extract_external_id(self, url: Optional[str]) -> Optional[str]:
        """The trailing numeric segment of an Eventbrite event URL, or None.

        This id is stable across renames (unlike the slug ahead of it) and
        unique per date occurrence — the reconciliation tier this scraper
        actually anchors identity on (see URL_IDENTITY above).
        """
        if not url:
            return None
        match = self._EVENT_ID_RE.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _coerce_price(raw) -> Optional[float]:
        """Eventbrite's offers carry price as a numeric string ("44.08")."""
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
