"""Motorco Music Hall scraper — WordPress + Tickera/FullCalendar plugin."""
import logging
import re
from datetime import datetime, date
from typing import Optional

import httpx

from app.scrapers.base import BaseScraper, ScrapedEvent, BROWSER_HEADERS

logger = logging.getLogger(__name__)


class MotorcoScraper(BaseScraper):
    """Scrape events from Motorco Music Hall's WordPress site.

    The calendar page embeds all events directly in the FullCalendar JS init
    as a JS array (single-quoted keys, not valid JSON). We extract each event
    using per-field regex instead of JSON parsing.

    Used by: Motorco Music Hall
    """

    async def scrape(self) -> list[ScrapedEvent]:
        url = self.config.get("url", "https://motorcomusic.com/calendar/")
        events = []
        today = date.today()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        # Each JS event object looks like:
        #   { title: 'Name', start: '2026-04-03 21:00', url: 'https://...', classNames: '...' }
        # Extract title, start, and url per event block.
        pattern = re.compile(
            r'\{[^{}]*?title\s*:\s*[\'"](.+?)[\'"]'
            r'[^{}]*?start\s*:\s*[\'"](\d{4}-\d{2}-\d{2}[^\'\"]*)[\'"]'
            r'[^{}]*?url\s*:\s*[\'"]([^\'\"]+)[\'"]',
            re.S,
        )

        seen = set()
        for m in pattern.finditer(html):
            raw_title, raw_start, raw_url = m.group(1), m.group(2), m.group(3)

            # Skip duplicates (the regex can match overlapping regions)
            key = (raw_title, raw_start)
            if key in seen:
                continue
            seen.add(key)

            parsed = self._parse_event(raw_title, raw_start, raw_url, today)
            if parsed:
                events.append(parsed)

        logger.info(f"[Motorco] Found {len(events)} upcoming events for {self.venue_slug}")
        return events

    def _parse_event(self, title: str, start_str: str, url: str, today: date) -> Optional[ScrapedEvent]:
        try:
            # Unescape HTML entities in title
            title = title.replace("&#038;", "&").replace("&amp;", "&").replace("&#8217;", "'")

            # Parse datetime — format is "2026-04-03 21:00" or "2026-04-03"
            dt = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(start_str.strip(), fmt)
                    break
                except ValueError:
                    continue

            if not dt:
                return None

            event_date = dt.date()
            if event_date < today:
                return None  # Skip past events

            show_time = dt.time() if dt.hour != 0 or dt.minute != 0 else None

            return ScrapedEvent(
                name=title,
                date=event_date,
                venue_slug=self.venue_slug,
                source="motorco",
                artist=title,
                show_time=show_time,
                ticket_url=url,
                source_url=url,
            )
        except Exception as e:
            logger.warning(f"[Motorco] Failed to parse event '{title}': {e}")
            return None
