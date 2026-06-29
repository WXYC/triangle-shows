"""
Scraper for Motorco Music Hall that extracts events from the venue's WordPress
calendar page by regex-parsing the embedded FullCalendar JS initialization data.

Role: One of many venue scrapers run in parallel by scrapers/manager.py, which is
triggered every 6 hours via POST /api/scrape (called by the scheduler or Cloud Scheduler).
Requires: httpx (HTTP client), app.scrapers.base (BaseScraper, ScrapedEvent, BROWSER_HEADERS).
"""

# --- Imports ---

import logging
import re
from datetime import datetime, date
from typing import Optional

import httpx

from app.scrapers.base import BaseScraper, ScrapedEvent, BROWSER_HEADERS

# --- Module-level setup ---

logger = logging.getLogger(__name__)


# --- Scraper class ---

class MotorcoScraper(BaseScraper):
    """Scrape events from Motorco Music Hall's WordPress site.

    The calendar page embeds all events directly in the FullCalendar JS init
    as a JS array (single-quoted keys, not valid JSON). We extract each event
    using per-field regex instead of JSON parsing.

    Used by: Motorco Music Hall
    """

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the Motorco calendar page and return upcoming ScrapedEvent objects."""
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

        # Deduplicate by (title, start) — the regex can produce overlapping matches
        # when event objects share similar surrounding HTML context.
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
        """Parse raw JS-extracted strings into a ScrapedEvent, or return None on failure."""
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

            # Only record show_time if a non-midnight time was actually specified
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
