"""Koka Booth Amphitheatre supplementary scraper — Carbonhouse CMS / ETIX.

Role: Runs as part of the scrape pipeline triggered by POST /api/scrape every 6 hours.
      Supplements the Ticketmaster scraper by fetching events posted directly on the
      venue's own Carbonhouse CMS site (boothamphitheatre.com) that may not appear on TM.
Requires: BaseScraper (app.scrapers.base) for the shared fetch_soup HTTP path.
          Venue config dict must supply a "url" key (defaults to boothamphitheatre.com/events).
"""

# --- Standard Library Imports ---
import json
import logging
from datetime import datetime, date, time
from typing import Optional

# --- Third-Party Imports ---
from bs4 import BeautifulSoup

# --- Internal Imports ---
from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

logger = logging.getLogger(__name__)


# --- Scraper Class ---

class KokaBoothScraper(BaseScraper):
    """Supplementary scraper for Koka Booth to catch ETIX-only shows.

    Scrapes the venue's own Carbonhouse CMS site for events not on Ticketmaster.
    Used by: Koka Booth Amphitheatre (supplementary to TM)
    """

    # Audit (issue #8): source_url is the event's own JSON-LD url or None - never the shared listing page (see _parse_jsonld).
    URL_IDENTITY = UrlIdentityVerdict.TRUSTED

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch and parse all events from boothamphitheatre.com."""
        url = self.config.get("url", "https://www.boothamphitheatre.com/events")
        events = []

        # Reuse one client for the listing page and any detail-page fetches below.
        # Routing through fetch_soup also means this scraper now sends the shared
        # browser headers, which the old bare client omitted.
        async with self.http_client() as client:
            soup = await self.fetch_soup(url, client=client)

            # Try JSON-LD first — most reliable if the CMS emits structured data
            events.extend(self._extract_jsonld(soup, url))

            # Parse event cards from Carbonhouse layout
            # Selector covers multiple Carbonhouse/Drupal card class variants
            cards = soup.select(
                ".event-card, .event-item, .event-listing, "
                ".views-row, article.event, .list-item"
            )

            for card in cards:
                parsed = self._parse_card(card)
                if parsed:
                    events.append(parsed)

            # Follow detail links if we found none — some CMS configs require
            # visiting individual event pages to get structured data
            if not events:
                links = soup.select("a[href*='event'], a[href*='show']")
                seen = set()
                for link in links:
                    href = link.get("href", "")
                    # Only follow links that stay on the venue's own domain
                    if href and href not in seen and "boothamphitheatre" in href:
                        seen.add(href)

                # Cap at 20 detail pages to avoid unbounded fetches
                for detail_url in list(seen)[:20]:
                    try:
                        d_soup = await self.fetch_soup(detail_url, client=client)
                        ld_events = self._extract_jsonld(d_soup, detail_url)
                        events.extend(ld_events)
                    except Exception as e:
                        logger.warning(f"[KokaBooth] Detail fetch failed: {e}")

        # Deduplicate by hash before returning (manager also deduplicates, but
        # this avoids inserting duplicate upserts within a single scrape run)
        seen_hashes = set()
        unique = []
        for ev in events:
            if ev.hash not in seen_hashes:
                seen_hashes.add(ev.hash)
                unique.append(ev)

        logger.info(f"[KokaBooth] Found {len(unique)} events for {self.venue_slug}")
        return unique

    # --- JSON-LD Extraction ---

    def _extract_jsonld(self, soup: BeautifulSoup, page_url: str) -> list[ScrapedEvent]:
        """Find and parse all JSON-LD Event/MusicEvent blocks on a page."""
        events = []
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                # JSON-LD can be a single object or an array of objects
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") in ("Event", "MusicEvent"):
                        parsed = self._parse_jsonld(item, page_url)
                        if parsed:
                            events.append(parsed)
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def _parse_jsonld(self, item: dict, page_url: str) -> Optional[ScrapedEvent]:
        """Convert a single JSON-LD Event dict into a ScrapedEvent."""
        try:
            name = item.get("name", "").strip()
            start = item.get("startDate", "")
            if not name or not start:
                return None

            # startDate may be a full ISO datetime ("2024-06-01T19:30:00") or
            # just a date ("2024-06-01") — handle both forms
            if "T" in start:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                event_date = dt.date()
                # Treat midnight as "no time specified" to avoid misleading 12:00am display
                show_time = dt.time().replace(tzinfo=None) if dt.time() != time(0, 0) else None
            else:
                event_date = date.fromisoformat(start[:10])
                show_time = None

            # image — see BaseScraper.extract_schema_image for the shape handling.
            image_url = self.extract_schema_image(item.get("image"))

            # offers can be a single Offer dict or a list; grab the first ticket URL
            offers = item.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            ticket_url = offers.get("url") if isinstance(offers, dict) else None

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="koka_booth",
                artist=name,

                show_time=show_time,
                ticket_url=ticket_url or page_url,
                image_url=image_url,
                # Identity: only the event's own JSON-LD url. page_url is the shared
                # listing page — as source_url it would alias every event on the
                # page under one identity (issue #8); it stays a ticket fallback only.
                source_url=item.get("url") or None,
            )
        except Exception as e:
            logger.warning(f"[KokaBooth] JSON-LD parse error: {e}")
            return None

    # --- HTML Card Parsing ---

    def _parse_card(self, card) -> Optional[ScrapedEvent]:
        """Extract event data from a Carbonhouse HTML event card element."""
        try:
            # Try linked heading first so we also capture the detail URL
            title_el = card.select_one("h2 a, h3 a, .event-title a, .title a")
            if not title_el:
                title_el = card.select_one("h2, h3, .event-title")
            if not title_el:
                return None

            name = title_el.get_text(strip=True)
            if not name:
                return None

            # Only grab href if the title element is actually an anchor
            link = title_el.get("href") if title_el.name == "a" else None

            date_el = card.select_one("time, .date, .event-date")
            event_date = None
            if date_el:
                # Prefer the machine-readable datetime attribute over display text
                dt_attr = date_el.get("datetime")
                if dt_attr:
                    try:
                        event_date = date.fromisoformat(dt_attr[:10])
                    except ValueError:
                        pass
                if not event_date:
                    # Display text (e.g. "Saturday, June 1, 2024"): parse_date
                    # strips the leading weekday name before the format walk.
                    event_date = self.parse_date(date_el.get_text(strip=True))

            # Skip cards where we couldn't determine a date
            if not event_date:
                return None

            img_el = card.select_one("img")
            image_url = img_el.get("src") if img_el else None

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="koka_booth",
                artist=name,

                ticket_url=link,
                image_url=image_url,
                source_url=link,
            )
        except Exception as e:
            logger.warning(f"[KokaBooth] Card parse error: {e}")
            return None
