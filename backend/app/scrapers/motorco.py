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
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---

logger = logging.getLogger(__name__)

# Each JS event object in the FullCalendar init array is a flat (non-nested)
# block like:
#   { title: 'Name', start: '2026-04-03 21:00', end: '2026-04-03 22:00',
#     url: 'https://...', classNames: '...', backgroundImage: 'https://...' }
# (a live fetch of https://motorcomusic.com/calendar/ on 2026-07-21 showed every
# event carrying `end` and `backgroundImage`, neither mentioned by the WordPress
# plugin's own inline comment.) Rather than chase key order with one long ordered
# regex, split the source into individual `{...}` blocks first, then pull each
# field out of a block independently by key — a reordered or newly-added key
# doesn't require touching this pattern.
_EVENT_OBJECT_PATTERN = re.compile(r"\{[^{}]*\}", re.S)
# Title is free text, so it must be matched as a proper JS string literal:
# WordPress `esc_js` backslash-escapes any apostrophe in the single-quoted value
# (a live fetch on 2026-07-21 showed 61 of 625 titles carrying a `\'`), and a
# naive `(.+?)['"]` terminates at that escaped quote — truncating e.g. "This Tour
# Won't Save You" to "This Tour Won\". Match the opening quote, then a run of
# escaped chars (`\\.`) or non-quote chars, up to the matching closing quote; the
# captured `val` still holds the backslashes, which `_parse_event` unescapes.
_TITLE_PATTERN = re.compile(
    r"title\s*:\s*(?P<q>['\"])(?P<val>(?:\\.|(?!(?P=q)).)*)(?P=q)", re.S
)
# start/url/backgroundImage values never contain a quote, so the simpler
# stop-at-first-quote form is sufficient (and clearer) for them.
_START_PATTERN = re.compile(r"start\s*:\s*['\"](\d{4}-\d{2}-\d{2}[^'\"]*)['\"]", re.S)
_URL_PATTERN = re.compile(r"\burl\s*:\s*['\"]([^'\"]+)['\"]", re.S)
_IMAGE_PATTERN = re.compile(r"backgroundImage\s*:\s*['\"]([^'\"]+)['\"]", re.S)
# JS string escape: a backslash followed by any single character (`\'`, `\"`,
# `\\`, `\/`). `re.sub` collapses each to the escaped character. esc_js only ever
# emits a backslash as an escape introducer, so this never eats a literal one.
_JS_ESCAPE_PATTERN = re.compile(r"\\(.)")


# --- Scraper class ---

class MotorcoScraper(BaseScraper):
    """Scrape events from Motorco Music Hall's WordPress site.

    The calendar page embeds all events directly in the FullCalendar JS init
    as a JS array (single-quoted keys, not valid JSON). We extract each event
    using per-field regex instead of JSON parsing.

    Used by: Motorco Music Hall
    """

    # Audit (issue #8): source_url is the per-event url from the calendar's JS event blocks (WordPress detail page; slug persists across renames).
    URL_IDENTITY = UrlIdentityVerdict.TRUSTED

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the Motorco calendar page and return upcoming ScrapedEvent objects."""
        url = self.config.get("url", "https://motorcomusic.com/calendar/")
        today = date.today()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        events = self._extract_events(html, today)

        logger.info(f"[Motorco] Found {len(events)} upcoming events for {self.venue_slug}")
        return events

    def _extract_events(self, html: str, today: date) -> list[ScrapedEvent]:
        """Parse the FullCalendar init JS embedded in the calendar page HTML.

        Splits the page into flat `{...}` object blocks and reads title/start/
        url/backgroundImage out of each block independently by key (see the
        module-level comment above the patterns). Deduplicates by (title,
        start) as a cheap guard against a page that lists the same event object
        more than once (e.g. across calendar views); `finditer` matches are
        already non-overlapping, so this only collapses genuine repeats.
        """
        events: list[ScrapedEvent] = []
        seen = set()
        for block_match in _EVENT_OBJECT_PATTERN.finditer(html):
            block = block_match.group(0)
            title_m = _TITLE_PATTERN.search(block)
            start_m = _START_PATTERN.search(block)
            url_m = _URL_PATTERN.search(block)
            if not (title_m and start_m and url_m):
                continue  # not an event block (or missing a required field)

            raw_title = title_m.group("val")
            raw_start, raw_url = start_m.group(1), url_m.group(1)

            key = (raw_title, raw_start)
            if key in seen:
                continue
            seen.add(key)

            image_m = _IMAGE_PATTERN.search(block)
            raw_image = image_m.group(1) if image_m else None

            parsed = self._parse_event(raw_title, raw_start, raw_url, today, raw_image)
            if parsed:
                events.append(parsed)

        return events

    def _parse_event(
        self,
        title: str,
        start_str: str,
        url: str,
        today: date,
        image_url: Optional[str] = None,
    ) -> Optional[ScrapedEvent]:
        """Parse raw JS-extracted strings into a ScrapedEvent, or return None on failure."""
        try:
            # Unescape HTML entities in title
            title = title.replace("&#038;", "&").replace("&amp;", "&").replace("&#8217;", "'")

            # Collapse JS string escapes the extractor preserved (esc_js turns an
            # apostrophe into \' inside the single-quoted title). Independent of
            # the entity pass above — escapes carry backslashes, entities don't.
            title = _JS_ESCAPE_PATTERN.sub(r"\1", title)

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
                image_url=image_url,
                source_url=url,
            )
        except Exception as e:
            logger.warning(f"[Motorco] Failed to parse event '{title}': {e}")
            return None
