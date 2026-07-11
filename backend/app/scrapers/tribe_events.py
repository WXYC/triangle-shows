"""
Scrape events from venues running The Events Calendar (Tribe Events) WordPress plugin,
using JSON-LD schema.org markup embedded in the page HTML.

Role: One of several venue scrapers called by scrapers/manager.py during each scrape cycle
(triggered every 6 hours via POST /api/scrape). This scraper is currently used by The Cave.
It fetches the venue's events listing page, extracts any JSON-LD Event objects found there,
then follows individual event detail links to pick up any events not represented on the listing page.

Requires: app.scrapers.base (BaseScraper, ScrapedEvent) for the shared fetch_soup HTTP
path; venue config must include a "url" key pointing to the venue's Tribe Events listing page.
"""

# --- Imports ---
import json
import logging
from datetime import datetime, date, time
from typing import Optional

from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---

logger = logging.getLogger(__name__)


# --- Scraper class ---

class TribeEventsScraper(BaseScraper):
    """Scrape events from The Events Calendar plugin via JSON-LD schema.org markup.

    Used by: The Cave
    """

    # Audit (issue #8): source_url is the event's own JSON-LD/detail-page url; The Events Calendar emits occurrence-specific URLs (date embedded for recurring events).
    URL_IDENTITY = UrlIdentityVerdict.TRUSTED

    async def scrape(self) -> list[ScrapedEvent]:
        url = self.config.get("url", "")
        if not url:
            raise ValueError(f"No URL configured for {self.venue_slug}")

        events = []

        # One client for the listing page plus every detail-page fetch below.
        async with self.http_client() as client:
            # First get the events listing page
            soup = await self.fetch_soup(url, client=client)

            # Extract JSON-LD from listing page
            events.extend(self._extract_jsonld_events(soup))

            # Also follow individual event links for more detail
            # Tribe Events uses different markup across plugin versions — try several selectors
            event_links = soup.select(
                ".tribe-events-calendar-list__event-title a, "
                ".tribe-events-list .tribe-events-list-event-title a, "
                ".tribe_events a.url, "
                "a.tribe-event-url, "
                "h2.tribe-events-list-event-title a"
            )

            if not event_links and not events:
                # Try alternate selectors for event links
                event_links = soup.select("article.tribe_events a[href*='event']")

            for link in event_links:
                href = link.get("href")
                # Skip missing or anchor-only links
                if not href or "#" in href:
                    continue

                try:
                    detail_soup = await self.fetch_soup(href, client=client)
                    # Pass the detail page URL so it can be used as fallback ticket/source URL
                    detail_events = self._extract_jsonld_events(detail_soup, source_url=href)
                    events.extend(detail_events)
                except Exception as e:
                    logger.warning(f"[Tribe] Failed to fetch detail page {href}: {e}")

        # Deduplicate by hash — listing page and detail pages may yield the same event
        seen = set()
        unique_events = []
        for ev in events:
            if ev.hash not in seen:
                seen.add(ev.hash)
                unique_events.append(ev)

        logger.info(f"[Tribe] Found {len(unique_events)} events for {self.venue_slug}")
        return unique_events

    # --- JSON-LD extraction helpers ---

    def _extract_jsonld_events(self, soup: BeautifulSoup, source_url: str = None) -> list[ScrapedEvent]:
        """Extract Event objects from JSON-LD script tags."""
        events = []
        scripts = soup.find_all("script", type="application/ld+json")

        for script in scripts:
            try:
                data = json.loads(script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue

            # Handle both single objects and arrays
            items = data if isinstance(data, list) else [data]

            for item in items:
                # Skip non-Event types (e.g. Organization, BreadcrumbList, WebSite)
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    if "Event" not in item_type and "MusicEvent" not in item_type:
                        continue
                elif item_type not in ("Event", "MusicEvent"):
                    continue

                parsed = self._parse_jsonld_event(item, source_url)
                if parsed:
                    events.append(parsed)

        return events

    def _parse_jsonld_event(self, data: dict, source_url: str = None) -> Optional[ScrapedEvent]:
        """Parse a single JSON-LD Event dict into a ScrapedEvent, returning None on failure."""
        try:
            name = data.get("name", "").strip()
            if not name:
                return None

            # --- Date / time parsing ---
            start = data.get("startDate", "")
            if not start:
                return None
            try:
                if "T" in start:
                    # Full ISO datetime — split into date and naive local time
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    event_date = dt.date()
                    show_time = dt.time().replace(tzinfo=None)
                else:
                    # Date-only string — no show time available
                    event_date = date.fromisoformat(start[:10])
                    show_time = None
            except ValueError:
                return None

            # Doors time from doorTime property
            doors_time = None
            door_str = data.get("doorTime", "")
            if door_str:
                doors_time = self.parse_time(door_str)

            # --- Performer extraction ---
            # First performer is treated as the headliner; the rest become support artists
            artist = None
            support = []
            performers = data.get("performer", [])
            if isinstance(performers, str):
                # Bare string — not useful as a structured performer list
                performers = []
            elif isinstance(performers, dict):
                performers = [performers]
            for i, p in enumerate(performers):
                p_name = p.get("name", "")
                if i == 0:
                    artist = p_name
                else:
                    support.append(p_name)

            # The structured first performer (when present) is the clean headliner
            # source — captured before the name fallback below so the manager only
            # falls back to heuristic name extraction when the source had none.
            headliner = artist or None

            # Fall back to event name when no performer is listed
            if not artist:
                artist = name

            # --- Ticket / pricing ---
            price_min = None
            price_max = None
            offers = data.get("offers", {})
            if isinstance(offers, list) and offers:
                # Use first offer when multiple are present
                offers = offers[0]
            if isinstance(offers, dict):
                price_min = offers.get("lowPrice") or offers.get("price")
                price_max = offers.get("highPrice") or price_min
                # Prices can come through as strings like "$15" — normalize them
                if isinstance(price_min, str):
                    price_min = self.parse_price(price_min)
                if isinstance(price_max, str):
                    price_max = self.parse_price(price_max)

                ticket_url = offers.get("url")
            else:
                ticket_url = None

            # --- Image ---
            image = data.get("image", "")
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                # Schema.org ImageObject — pull out the URL string
                image = image.get("url", "")
            image_url = image or None

            # Description
            description = data.get("description", "")

            # --- Status ---
            event_status = data.get("eventStatus", "")
            if "Cancelled" in event_status:
                status = "cancelled"
            elif price_min == 0:
                status = "free"
            else:
                status = "on_sale"

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="tribe_events",
                artist=artist,
                headliner=headliner,
                support_artists=", ".join(support) if support else None,
                doors_time=doors_time,
                show_time=show_time,
                ticket_url=ticket_url or source_url,
                price_min=price_min,
                price_max=price_max,
                image_url=image_url,
                status=status,
                description=description[:500] if description else None,
                # Identity: the event's own JSON-LD url first — The Events Calendar
                # emits occurrence-specific URLs for recurring events, and the passed
                # detail-page href must not override them (a detail page can embed
                # several Event items that would otherwise all share the href).
                source_url=data.get("url") or source_url,
            )
        except Exception as e:
            logger.warning(f"[Tribe] Failed to parse JSON-LD event: {e}")
            return None
