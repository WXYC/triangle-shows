"""Webflow CMS scraper for venues that embed events in a CMS collection list.

Role: One of several venue-specific scraper implementations; instantiated and run
by scrapers/manager.py when the scheduler triggers POST /api/scrape every 6 hours.
Requires: app.scrapers.base (BaseScraper, ScrapedEvent) for the shared fetch_soup HTTP
path and parse_date helper; a venue config dict with at least a 'url' key.
Currently used by: Pour House.
"""

# --- Imports ---
import logging
import re

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module Setup ---
logger = logging.getLogger(__name__)


# --- Scraper Class ---

class WebflowCMSScraper(BaseScraper):
    """Scrape events from a Webflow CMS collection list embedded in the page HTML.

    Used by: Pour House

    Config keys:
        url             - calendar page URL
        base_url        - base URL for constructing event links
        item_selector   - CSS selector for each event item (default: .show-collection-item)
        name_selector   - CSS selector for event name within item (default: .show-name)
        date_selector   - CSS selector for event date within item (default: .show-start-date)
        slug_selector   - CSS selector for slug within item (default: .show-slug)
        shows_path      - path prefix for event pages (default: /shows/)
        date_format     - strptime format string (default: %B %d, %Y)
        image_selector  - CSS selector for the <img> within an event's detail-page
                           link (default: img)

    Image note (issue #56): the live Pour House page renders every show twice —
    once in the `.show-collection-item` list this scraper otherwise parses (name/
    date/slug only, confirmed to carry no image), and again in a separate Webflow
    "grid" list whose entries wrap an `<a href="{shows_path}<slug>">` around the
    show-flyer `<img>`. There is no image inside item_selector's own elements, so
    extraction cross-references the two lists by slug rather than doing a plain
    `item.select_one(image_selector)`.
    """

    # Audit (issue #8): source_url is the ticket link, which is not guaranteed event-unique across a venue's listings.
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the venue's Webflow page and extract events from the CMS collection markup."""
        url = self.config.get("url", "")
        if not url:
            raise ValueError(f"No URL configured for {self.venue_slug}")

        soup = await self.fetch_soup(url)
        events = self._parse_soup(soup)

        logger.info(f"[WebflowCMS] Found {len(events)} events for {self.venue_slug}")
        return events

    def _parse_soup(self, soup) -> list[ScrapedEvent]:
        """Extract events from a fetched (or test-constructed) page soup."""
        # Pull selector/format overrides from config, falling back to Pour House defaults
        base_url = self.config.get("base_url", "").rstrip("/")
        item_sel = self.config.get("item_selector", ".show-collection-item")
        name_sel = self.config.get("name_selector", ".show-name")
        date_sel = self.config.get("date_selector", ".show-start-date")
        slug_sel = self.config.get("slug_selector", ".show-slug")
        shows_path = self.config.get("shows_path", "/shows/")
        date_fmt = self.config.get("date_format", "%B %d, %Y")
        image_sel = self.config.get("image_selector", "img")

        events = []

        image_by_slug = self._build_image_map(soup, shows_path, image_sel)

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

            # Route through the shared parser using this venue's configured
            # format; it also tolerates a leading weekday / ordinal suffix.
            event_date = self.parse_date(date_str, formats=[date_fmt])
            if not event_date:
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

            # Best-effort only: a slug with no counterpart in the grid list (markup
            # drift, a show pulled from the grid but not the calendar, etc.) just
            # yields None — never raises, never drops the event (issue #56).
            image_url = image_by_slug.get(slug) if slug else None

            events.append(ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="webflow_cms",
                artist=name,
                ticket_url=ticket_url,
                source_url=ticket_url,
                age_restriction=age_restriction,
                image_url=image_url,
            ))

        return events

    @staticmethod
    def _build_image_map(soup, shows_path: str, image_sel: str) -> dict[str, str]:
        """Map each show's slug to its flyer image URL from the Webflow grid list.

        The grid list is a separate `.uui-layout88_item-2 w-dyn-item` collection
        elsewhere on the same page; each entry wraps an `<a href="{shows_path}
        <slug>">` around the flyer `<img>`. Scoping by `a[href^=shows_path]`
        (rather than hardcoding the grid list's own auto-generated Webflow class)
        keeps this resilient to a page redesign that renames the grid wrapper but
        keeps the shows_path link convention.
        """
        image_by_slug: dict[str, str] = {}
        if not shows_path:
            return image_by_slug

        for link_el in soup.select(f"a[href^='{shows_path}']"):
            href = link_el.get("href") or ""
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            if not slug:
                continue

            img_el = link_el.select_one(image_sel)
            if not img_el:
                continue

            src = img_el.get("src") or img_el.get("data-src")
            if src and slug not in image_by_slug:
                image_by_slug[slug] = src

        return image_by_slug
