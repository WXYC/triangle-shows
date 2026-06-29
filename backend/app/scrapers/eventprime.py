"""
Scraper for Kings Raleigh using their custom WordPress theme with a server-rendered show table.

Role: Instantiated and called by scrapers/manager.py during each scrape cycle (triggered every
      6 hours via POST /api/scrape). The class name reflects an earlier EventPrime implementation
      that was replaced; the actual target is Kings Raleigh's hand-rolled WordPress theme.
Requires: app.scrapers.base (BaseScraper, ScrapedEvent, BROWSER_HEADERS); httpx and
          beautifulsoup4/lxml must be installed; no venue-specific env vars needed beyond
          the optional "url" key in the venue's scraper config.
"""

# --- Imports ---
import logging
import re
from datetime import datetime, date
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedEvent, BROWSER_HEADERS

# --- Module-level setup ---
logger = logging.getLogger(__name__)


# --- Scraper class ---

class EventPrimeScraper(BaseScraper):
    """Scrape events from Kings Raleigh's website.

    Kings uses a custom WordPress theme (not EventPrime despite the class name).
    Events are server-side rendered in a <table id="Shows"> with rows containing:
      - p.date        → "Thursday, February 26th, 2026"
      - h3            → event title (may have <strong> presenter prefix and <em> support)
      - p Time:       → show time
      - p Doors:      → doors time
      - p Admission:  → ticket price
      - a.tickets     → ticket purchase link

    Used by: Kings
    """

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the Kings homepage, parse the Shows table, and return deduplicated events."""
        url = self.config.get("url", "https://www.kingsraleigh.com/")
        events = []

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

        shows_table = soup.find("table", id="Shows")
        if not shows_table:
            logger.warning("[Kings] Could not find <table id='Shows'>")
            return []

        for row in shows_table.select("tr"):
            parsed = self._parse_row(row)
            if parsed:
                events.append(parsed)

        # Deduplicate by hash in case the same event appears more than once on the page
        seen = set()
        unique = []
        for ev in events:
            if ev.hash not in seen:
                seen.add(ev.hash)
                unique.append(ev)

        logger.info(f"[Kings] Found {len(unique)} events for {self.venue_slug}")
        return unique

    def _parse_row(self, row) -> Optional[ScrapedEvent]:
        """Extract a single ScrapedEvent from one <tr> in the Shows table, or None if invalid."""
        try:
            # --- Date ---
            date_el = row.select_one("p.date")
            if not date_el:
                return None
            event_date = self._parse_date(date_el.get_text(strip=True))
            if not event_date:
                return None

            # --- Title — strip presenter <strong> and support <em> ---
            title_el = row.select_one("td.body h3")
            if not title_el:
                return None
            # Remove <strong> (presenter) and <em> (support) from title
            for tag in title_el.find_all(["strong", "em"]):
                tag.decompose()
            name = title_el.get_text(strip=True)
            if not name:
                return None

            # --- Links ---
            ticket_el = row.select_one("a.tickets")
            ticket_url = ticket_el.get("href") if ticket_el else None

            # Fall back to the first anchor in the body cell as the canonical event URL
            body_td = row.select_one("td.body")
            detail_link = body_td.select_one("a[href]") if body_td else None
            source_url = detail_link.get("href") if detail_link else None

            # --- Times and admission — scan all <p> tags in the body cell ---
            show_time = None
            doors_time = None
            price_min = None
            price_max = None

            for p in row.select("td.body p"):
                text = p.get_text(strip=True)
                if text.startswith("Time:"):
                    show_time = self.parse_time(text[5:].strip())
                elif text.startswith("Doors:"):
                    doors_time = self.parse_time(text[6:].strip())
                elif text.startswith("Admission:"):
                    price_min, price_max = self.parse_price_range(text[10:].strip())

            # --- Image ---
            img_el = row.select_one("td.img img")
            image_url = img_el.get("src") if img_el else None

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="kings",
                artist=name,
                show_time=show_time,
                doors_time=doors_time,
                ticket_url=ticket_url,
                price_min=price_min,
                price_max=price_max,
                image_url=image_url,
                source_url=source_url,
            )
        except Exception as e:
            logger.warning(f"[Kings] Failed to parse row: {e}")
            return None

    @staticmethod
    def _parse_date(text: str) -> Optional[date]:
        """Parse e.g. 'Thursday, February 26th, 2026' → date."""
        # Strip ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
        text = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', text)
        # Strip weekday prefix
        text = re.sub(r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*', '', text)
        text = text.strip().rstrip(",")
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None
