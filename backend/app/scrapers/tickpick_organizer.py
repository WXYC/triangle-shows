"""TickPick organizer page scraper — parses events from JSON-LD on organizer pages.

Role: Venue scraper invoked by the scrape manager (scrapers/manager.py) during a
POST /api/scrape cycle. This scraper handles venues that sell tickets through TickPick
and expose event data as schema.org JSON-LD on their organizer profile page.
Requires: app.scrapers.base (BaseScraper, ScrapedEvent) for the shared fetch_soup HTTP
path; venue config must include an "organizer_id" key.
"""
# --- Imports ---
import json
import logging
from datetime import datetime, date
from typing import Optional

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---
logger = logging.getLogger(__name__)


# --- Scraper class ---

class TickPickOrganizerScraper(BaseScraper):
    """Scrape events from a TickPick organizer page via JSON-LD schema.org markup.

    Used by: Chapel of Bones
    Config: {"organizer_id": "chapel-of-bones"}
    """

    # Audit (issue #8): source_url is the TickPick ticket page; event-uniqueness across an organizer's listings is unverified.
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the TickPick organizer page and extract all upcoming events."""
        organizer_id = self.config.get("organizer_id", "")
        if not organizer_id:
            raise ValueError(f"No organizer_id configured for {self.venue_slug}")

        url = f"https://www.tickpick.com/organizer/o/{organizer_id}"

        soup = await self.fetch_soup(url)

        events = []
        # TickPick embeds event data as one or more <script type="application/ld+json"> blocks
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue

            # A single JSON-LD block may be a dict or a list of objects
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("@type", "")

                # Organization with nested event array
                if item_type == "Organization" and "event" in item:
                    # Events can be a single dict or a list; normalise to list
                    nested = item["event"]
                    if isinstance(nested, dict):
                        nested = [nested]
                    for ev_data in nested:
                        parsed = self._parse_event(ev_data)
                        if parsed:
                            events.append(parsed)

                # Top-level Event or MusicEvent
                elif item_type in ("Event", "MusicEvent"):
                    parsed = self._parse_event(item)
                    if parsed:
                        events.append(parsed)

        logger.info(f"[TickPickOrganizer] Found {len(events)} events for {self.venue_slug}")
        return events

    def _parse_event(self, data: dict) -> Optional[ScrapedEvent]:
        """Parse a single schema.org Event dict into a ScrapedEvent, or return None if invalid."""
        try:
            name = data.get("name", "").strip()
            if not name:
                return None

            start = data.get("startDate", "")
            if not start:
                return None

            try:
                if "T" in start:
                    # TickPick marks local times as Z; treat as local without converting.
                    dt = datetime.fromisoformat(start.replace("Z", ""))
                    event_date = dt.date()
                    show_time = dt.time()
                else:
                    # Date-only string (no time component)
                    event_date = date.fromisoformat(start[:10])
                    show_time = None
            except ValueError:
                return None

            # Skip past events
            if event_date < date.today():
                return None

            ticket_url = data.get("url")

            # schema.org's `image` is polymorphic (bare URL, list, ImageObject);
            # BaseScraper.extract_schema_image normalizes it to a URL or None.
            image_url = self.extract_schema_image(data.get("image"))

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="tickpick_organizer",
                artist=name,
                show_time=show_time,
                ticket_url=ticket_url,
                image_url=image_url,
                source_url=ticket_url,
            )
        except Exception as e:
            logger.warning(f"[TickPickOrganizer] Failed to parse event: {e}")
            return None
