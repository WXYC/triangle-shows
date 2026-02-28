"""The Events Calendar / JSON-LD scraper for The Cave."""
import json
import logging
from datetime import datetime, date, time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedEvent, BROWSER_HEADERS

logger = logging.getLogger(__name__)


class TribeEventsScraper(BaseScraper):
    """Scrape events from The Events Calendar plugin via JSON-LD schema.org markup.

    Used by: The Cave
    """

    async def scrape(self) -> list[ScrapedEvent]:
        url = self.config.get("url", "")
        if not url:
            raise ValueError(f"No URL configured for {self.venue_slug}")

        events = []

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            # First get the events listing page
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # Extract JSON-LD from listing page
            events.extend(self._extract_jsonld_events(soup))

            # Also follow individual event links for more detail
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
                if not href or "#" in href:
                    continue

                try:
                    detail_resp = await client.get(href)
                    detail_resp.raise_for_status()
                    detail_soup = BeautifulSoup(detail_resp.text, "lxml")
                    detail_events = self._extract_jsonld_events(detail_soup, source_url=href)
                    events.extend(detail_events)
                except Exception as e:
                    logger.warning(f"[Tribe] Failed to fetch detail page {href}: {e}")

        # Deduplicate by hash
        seen = set()
        unique_events = []
        for ev in events:
            if ev.hash not in seen:
                seen.add(ev.hash)
                unique_events.append(ev)

        logger.info(f"[Tribe] Found {len(unique_events)} events for {self.venue_slug}")
        return unique_events

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
                # Skip non-Event types
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
        try:
            name = data.get("name", "").strip()
            if not name:
                return None

            # Date
            start = data.get("startDate", "")
            if not start:
                return None
            try:
                if "T" in start:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    event_date = dt.date()
                    show_time = dt.time().replace(tzinfo=None)
                else:
                    event_date = date.fromisoformat(start[:10])
                    show_time = None
            except ValueError:
                return None

            # Doors time from doorTime property
            doors_time = None
            door_str = data.get("doorTime", "")
            if door_str:
                doors_time = self.parse_time(door_str)

            # Performer
            artist = None
            support = []
            performers = data.get("performer", [])
            if isinstance(performers, str):
                performers = []
            elif isinstance(performers, dict):
                performers = [performers]
            for i, p in enumerate(performers):
                p_name = p.get("name", "")
                if i == 0:
                    artist = p_name
                else:
                    support.append(p_name)

            if not artist:
                artist = name

            # Price
            price_min = None
            price_max = None
            offers = data.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price_min = offers.get("lowPrice") or offers.get("price")
                price_max = offers.get("highPrice") or price_min
                if isinstance(price_min, str):
                    price_min = self.parse_price(price_min)
                if isinstance(price_max, str):
                    price_max = self.parse_price(price_max)

                ticket_url = offers.get("url")
            else:
                ticket_url = None

            # Image
            image = data.get("image", "")
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url", "")
            image_url = image or None

            # Description
            description = data.get("description", "")

            # Status
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
                support_artists=", ".join(support) if support else None,
                doors_time=doors_time,
                show_time=show_time,
                ticket_url=ticket_url or source_url,
                price_min=price_min,
                price_max=price_max,
                image_url=image_url,
                status=status,
                description=description[:500] if description else None,
                source_url=source_url or data.get("url"),
            )
        except Exception as e:
            logger.warning(f"[Tribe] Failed to parse JSON-LD event: {e}")
            return None
