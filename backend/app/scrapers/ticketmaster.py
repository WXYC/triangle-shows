"""
Ticketmaster Discovery API scraper — fetches events for configured venues by
attraction/venue ID and returns normalized ScrapedEvent objects.

Role: One of several venue scrapers invoked by scrapers/manager.py during each
scheduled scrape cycle (POST /api/scrape). Unlike HTML scrapers, this one hits
the Ticketmaster REST API rather than parsing web pages, so it handles
pagination and API-specific quirks (duplicate package listings, upsell items).

Requires: TICKETMASTER_API_KEY env var (loaded via config.py); httpx for async
HTTP; app.scrapers.base.BaseScraper and ScrapedEvent for the shared interface.
"""
import logging
import re
from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional

import httpx

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---

logger = logging.getLogger(__name__)

# Base URL for Ticketmaster Discovery API v2
TM_BASE_URL = "https://app.ticketmaster.com/discovery/v2"


# --- Scraper class ---

class TicketmasterScraper(BaseScraper):
    """Scrape events from Ticketmaster Discovery API v2."""

    # Audit (issue #8): source_url is the ticket page (not guaranteed event-unique); identity comes from external_id, the Ticketmaster event id.
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    def __init__(self, venue_slug: str, venue_tm_id: str, api_key: str, config: Optional[dict] = None):
        super().__init__(venue_slug, config)
        self.venue_tm_id = venue_tm_id
        self.api_key = api_key

    # --- Main scrape logic ---

    async def scrape(self) -> list[ScrapedEvent]:
        events = []
        now = datetime.utcnow()
        # Fetch events starting now through the next 6 months
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_dt = (now + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "apikey": self.api_key,
            "venueId": self.venue_tm_id,
            "startDateTime": start_dt,
            "endDateTime": end_dt,
            "size": 200,  # max results per page
            "page": 0,
            "sort": "date,asc",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                logger.info(f"[TM] Fetching page {params['page']} for {self.venue_slug}")
                resp = await client.get(f"{TM_BASE_URL}/events.json", params=params)
                resp.raise_for_status()
                data = resp.json()

                # TM wraps results in an _embedded envelope; absent when no events found
                embedded = data.get("_embedded", {})
                raw_events = embedded.get("events", [])

                if not raw_events:
                    break

                # Deduplicate package/tier variants before parsing to avoid storing duplicates
                for ev in self._dedup_raw_events(raw_events):
                    parsed = self._parse_event(ev)
                    if parsed:
                        events.append(parsed)

                # Check pagination
                page_info = data.get("page", {})
                total_pages = page_info.get("totalPages", 1)
                current_page = page_info.get("number", 0)

                if current_page + 1 >= total_pages:
                    break
                params["page"] = current_page + 1

        logger.info(f"[TM] Found {len(events)} events for {self.venue_slug}")
        return events

    # --- Name normalization patterns (used for deduplication) ---

    # Suffixes like "Boxes 3/17 at 7:30pm" or bare "Boxes" appended by DPAC box-office listings
    _BOXES_RE = re.compile(r'\s+Boxes?(?:\s.*)?$', re.IGNORECASE)
    # "Add-Ons: " prefix on upsell items
    _ADDONS_RE = re.compile(r'^Add-Ons?\s*:\s*', re.IGNORECASE)
    # Generic upsell/pass events that should never appear as shows
    _SKIP_NAMES = {
        "president's club guest pass",
        "vip package",
        "meet & greet",
    }

    @classmethod
    def _normalize_name(cls, name: str) -> str:
        """Strip packaging suffixes/prefixes and return a lowercased base name."""
        name = cls._ADDONS_RE.sub('', name)
        name = cls._BOXES_RE.sub('', name)
        # Drop dash-separated package tiers (e.g. "Hamilton - VIP" -> "hamilton")
        return name.split(' - ')[0].strip().lower()

    @classmethod
    def _dedup_raw_events(cls, raw_events: list) -> list:
        """Deduplicate package/tier variants of the same show.

        DPAC's TM feed returns multiple entries per show in several forms:
          - "Hamilton" + "Hamilton - VIP" (dash-separated packages)
          - "Seth Meyers: Live" + "Seth Meyers: Live Boxes 2/27 at 7:00pm"
          - "Add-Ons: Lucy Darling: You're Welcome" + "Lucy Darling: You're Welcome"
          - "President's Club Guest Pass" (generic upsell — skip entirely)

        We normalise names before keying and prefer the cleanest entry.
        """
        seen: dict = {}  # key -> chosen raw event

        for ev in raw_events:
            name = ev.get("name", "")
            norm = cls._normalize_name(name)

            # Drop generic upsell/pass items
            if norm in cls._SKIP_NAMES:
                continue

            # Key on (normalized name, date) so the same show on different dates is kept separate
            date_str = ev.get("dates", {}).get("start", {}).get("localDate", "")
            key = (norm, date_str)

            if key not in seen:
                seen[key] = ev
            else:
                # Prefer the entry whose raw name is the shortest clean version
                existing_name = seen[key].get("name", "")
                if len(name) < len(existing_name):
                    seen[key] = ev

        # Apply clean names to surviving events: strip Box/Boxes date suffixes so
        # "Hamilton Boxes 3/17 at 7:30pm" becomes "Hamilton" even when no clean
        # variant existed in the feed.
        result = []
        for ev in seen.values():
            name = ev.get("name", "")
            clean = cls._ADDONS_RE.sub("", name)
            clean = cls._BOXES_RE.sub("", clean).split(" - ")[0].strip()
            # Only mutate a copy to avoid modifying the original shared dict
            if clean and clean != name:
                ev = dict(ev)
                ev["name"] = clean
            result.append(ev)
        return result

    # --- Event parsing ---

    def _parse_event(self, ev: dict) -> Optional[ScrapedEvent]:
        """Parse a single raw TM event dict into a ScrapedEvent, returning None on failure."""
        try:
            name = ev.get("name", "").strip()
            if not name:
                return None

            # Date/time
            dates = ev.get("dates", {})
            start = dates.get("start", {})
            date_str = start.get("localDate")
            if not date_str:
                return None
            event_date = date.fromisoformat(date_str)

            show_time = None
            time_str = start.get("localTime")
            if time_str:
                try:
                    show_time = dt_time.fromisoformat(time_str)
                except ValueError:
                    pass

            # Artists — TM lists headliner first, support acts after
            artist = None
            support = []
            attractions = ev.get("_embedded", {}).get("attractions", [])
            if attractions:
                artist = attractions[0].get("name")
                support = [a.get("name") for a in attractions[1:] if a.get("name")]

            # Prices
            price_min = None
            price_max = None
            price_ranges = ev.get("priceRanges", [])
            if price_ranges:
                price_min = price_ranges[0].get("min")
                price_max = price_ranges[0].get("max")

            # Genre (from TM classifications)
            genre = None
            subgenre = None
            classifications = ev.get("classifications", [])
            if classifications:
                cls = classifications[0]
                genre_obj = cls.get("genre", {})
                # TM uses "Undefined" as a placeholder when genre is unknown
                if genre_obj and genre_obj.get("name") != "Undefined":
                    genre = genre_obj.get("name")
                sub_obj = cls.get("subGenre", {})
                if sub_obj and sub_obj.get("name") != "Undefined":
                    subgenre = sub_obj.get("name")

            # Image - prefer 16:9 ratio
            image_url = None
            images = ev.get("images", [])
            ratio_16_9 = [i for i in images if i.get("ratio") == "16_9"]
            if ratio_16_9:
                # Pick largest 16:9
                best = max(ratio_16_9, key=lambda i: i.get("width", 0))
                image_url = best.get("url")
            elif images:
                image_url = images[0].get("url")

            # Status — map TM status codes to our internal values
            status_code = dates.get("status", {}).get("code", "")
            if status_code == "offsale":
                status = "sold_out"
            elif status_code == "cancelled":
                status = "cancelled"
            else:
                status = "on_sale"

            # Age restriction
            age = None
            age_restrictions = ev.get("ageRestrictions", {})
            if age_restrictions.get("legalAgeEnforced"):
                age = "21+"

            # Ticket URL
            ticket_url = ev.get("url")

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="ticketmaster",
                external_id=ev.get("id"),
                artist=artist,
                # Structured clean performer: TM's first attraction is the headliner
                # (None when the event has no attractions — the manager then derives
                # one heuristically from the name).
                headliner=artist,
                support_artists=", ".join(support) if support else None,
                show_time=show_time,
                ticket_url=ticket_url,
                price_min=price_min,
                price_max=price_max,
                image_url=image_url,
                genre=genre,
                subgenre=subgenre,
                status=status,
                age_restriction=age,
                source_url=ticket_url,
            )
        except Exception as e:
            logger.warning(f"[TM] Failed to parse event: {e}")
            return None
