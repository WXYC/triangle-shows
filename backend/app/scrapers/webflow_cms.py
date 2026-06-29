"""Webflow CMS scraper for venues that embed events in a CMS collection list.

Role: One of several venue-specific scraper implementations; instantiated and run
by scrapers/manager.py when the scheduler triggers POST /api/scrape every 6 hours.
Requires: httpx, beautifulsoup4/lxml, and a venue config dict with at least a 'url' key.
Currently used by: Pour House.
"""

# --- Imports ---
import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedEvent, BROWSER_HEADERS

# --- Module Setup ---
logger = logging.getLogger(__name__)


# --- Scraper Class ---

class WebflowCMSScraper(BaseScraper):
    """Scrape events from a Webflow CMS collection list embedded in the page HTML.

    Used by: Pour House

    Config keys:
        url            - calendar page URL
        base_url       - base URL for constructing event links
        item_selector  - CSS selector for each event item (default: .show-collection-item)
        name_selector  - CSS selector for event name within item (default: .show-name)
        date_selector  - CSS selector for event date within item (default: .show-start-date)
        slug_selector  - CSS selector for slug within item (default: .show-slug)
        shows_path     - path prefix for event pages (default: /shows/)
        date_format    - strptime format string (default: %B %d, %Y)
    """

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the venue's Webflow page and extract events from the CMS collection markup."""
        url = self.config.get("url", "")
        if not url:
            raise ValueError(f"No URL configured for {self.venue_slug}")

        # Pull selector/format overrides from config, falling back to Pour House defaults
        base_url = self.config.get("base_url", "").rstrip("/")
        item_sel = self.config.get("item_selector", ".show-collection-item")
        name_sel = self.config.get("name_selector", ".show-name")
        date_sel = self.config.get("date_selector", ".show-start-date")
        slug_sel = self.config.get("slug_selector", ".show-slug")
        shows_path = self.config.get("shows_path", "/shows/")
        date_fmt = self.config.get("date_format", "%B %d, %Y")

        events = []

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # --- Parse Each Event Item ---
            for item in soup.select(item_sel):
                name_el = item.select_one(name_sel)
                date_el = item.select_one(date_sel)
                slug_el = item.select_one(slug_sel)

                # Skip items missing required fields
                if not name_el or not date_el:
                    continue

                name = name_el.get_text(strip=True)
                date_str = date_el.get_text(strip=True)
                # Slug is optional — used only for constructing the ticket URL
                slug = slug_el.get_text(strip=True) if slug_el else None

                if not name or not date_str:
                    continue

                try:
                    event_date = datetime.strptime(date_str, date_fmt).date()
                except ValueError:
                    logger.warning(f"[WebflowCMS] Cannot parse date '{date_str}' for {self.venue_slug}")
                    continue

                # Build the event detail URL only when both base URL and slug are available
                ticket_url = f"{base_url}{shows_path}{slug}" if (base_url and slug) else None

                # Extract age restriction from name prefix like "(18+) Artist Name"
                age_restriction = None
                age_match = re.match(r'^\((\d+\+)\)\s*', name)
                if age_match:
                    age_restriction = age_match.group(1)
                    # Strip the age prefix so the stored name is just the artist/show title
                    name = name[age_match.end():]

                events.append(ScrapedEvent(
                    name=name,
                    date=event_date,
                    venue_slug=self.venue_slug,
                    source="webflow_cms",
                    artist=name,
                    ticket_url=ticket_url,
                    source_url=ticket_url,
                    age_restriction=age_restriction,
                ))

        logger.info(f"[WebflowCMS] Found {len(events)} events for {self.venue_slug}")
        return events
