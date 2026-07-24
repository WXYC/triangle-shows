"""Scraper for Nectar Lounge (Seattle), reading the venue's own homepage
schema.org Event JSON-LD rather than its ticketer (Tixr).

Role: One of many venue scrapers run in parallel by scrapers/manager.py, which is
triggered every 6 hours via POST /api/scrape (called by the scheduler or Cloud
Scheduler). Motivated by SeattleShows.net (epic #61): tixr.com itself is
DataDome-walled (issue #69 — a live 2026-07-24 check found plain httpx AND
curl_cffi with a Chrome JA3 fingerprint both got a byte-identical 403 stub, and
Tixr loads its events client-side from an authenticated Studio API a Tier-3
unblocker still can't reach). nectarlounge.com (WordPress) sidesteps that
entirely by publishing the same events as schema.org JSON-LD directly in its
server-rendered HTML, confirmed ungated (HTTP 200) the same day.
Requires: httpx (via BaseScraper.fetch_soup), app.scrapers.base (BaseScraper,
ScrapedEvent), app.scrapers.identity (UrlIdentityVerdict).
"""

# --- Imports ---

import json
import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---

logger = logging.getLogger(__name__)

# nectarlounge.com's WordPress JSON-LD plugin emits startDate/endDate as
# genuine UTC instants (a real "+00:00", not a mislabeled local time) — but every
# event's *date* the venue actually means is the Seattle-local calendar date,
# which differs from the UTC date for any evening show. Confirmed against three
# independent events' own description text on a 2026-07-24 live fetch: "Mo' Jam
# Mondays" carries startDate "2026-07-28T02:30:00+00:00" (a Tuesday in UTC) while
# its description literally says "7.27 Monday" — converting the UTC instant to
# America/Los_Angeles lands on 2026-07-27 19:30 local, matching. So every
# startDate here is parsed as aware UTC and then converted (not reinterpreted)
# to Seattle local time before its date/time components are read.
_SEATTLE_TZ = ZoneInfo("America/Los_Angeles")

# location.name on nectarlounge.com's JSON-LD is not exclusively "Nectar Lounge":
# a live fetch on 2026-07-24 showed the feed also carrying "Hidden Hall" events
# (hiddenhall.com currently aliases Nectar's own calendar — see issue #69, a
# Hidden Hall scraper is explicitly out of scope, tracked as a #73 follow-up).
# Matched case-insensitively against the exact venue name, not a substring, so a
# future feed permutation doesn't accidentally admit or exclude events.
_NECTAR_LOCATION_NAME = "nectar lounge"

# The event's own tixr.com URL ends in "-<numeric id>" (e.g.
# ".../sour-times-a-tribute-to-portishead-195530"); that trailing id is Tixr's
# stable per-event identifier (issue #69's audited ext: tier — see URL_IDENTITY
# below), independent of the human-readable slug ahead of it.
_EXTERNAL_ID_RE = re.compile(r"-(\d+)/?(?:[?#].*)?$")


# --- Scraper class ---

class NectarScraper(BaseScraper):
    """Scrape Nectar Lounge's own homepage JSON-LD (bypasses Tixr's DataDome wall).

    nectarlounge.com embeds every upcoming event — its own and, per the Hidden
    Hall aliasing above, some that aren't its own — as one JSON array inside a
    single ``<script type="application/ld+json">`` block. Events are filtered to
    ``location.name == "Nectar Lounge"`` (case-insensitive) so a Hidden Hall
    event never gets attributed to this venue.

    Used by: Nectar Lounge (Seattle)
    """

    # Audit (issue #69): source_url is the event's own tixr.com ticket page, the
    # only per-event URL nectarlounge.com's JSON-LD exposes (there is no
    # nectarlounge.com detail page to fall back to). Tixr's slug is derived from
    # the event name (evidenced by the differing slugs across similarly-named
    # recurring events like "Mo' Jam Mondays"), and this scraper has no evidence
    # either way on whether Tixr preserves that slug across a rename — so the
    # URL text itself is not asserted rename-stable. The trailing numeric id
    # (extracted as external_id below) IS the durable identity Tixr guarantees,
    # and derive_source_key already prefers ext: over url: regardless of this
    # verdict — same shape as the eventbrite.py audit.
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch nectarlounge.com and return its own (non-Hidden-Hall) events."""
        url = self.config.get("url", "https://www.nectarlounge.com/")

        soup = await self.fetch_soup(url)
        events = self._extract_jsonld_events(soup)

        logger.info(f"[Nectar] Found {len(events)} events for {self.venue_slug}")
        return events

    def _extract_jsonld_events(self, soup: BeautifulSoup) -> list[ScrapedEvent]:
        """Find the page's JSON-LD block(s) and parse each Nectar Lounge Event."""
        events = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue

            # JSON-LD may be a single object or an array of objects; nectarlounge.com
            # emits one array covering the whole calendar (see class docstring).
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    if "Event" not in item_type and "MusicEvent" not in item_type:
                        continue
                elif item_type not in ("Event", "MusicEvent"):
                    continue
                if not self._is_nectar_location(item.get("location")):
                    continue
                parsed = self._parse_jsonld_event(item)
                if parsed:
                    events.append(parsed)
        return events

    @staticmethod
    def _is_nectar_location(location) -> bool:
        """True only when location.name is exactly "Nectar Lounge" (see
        _NECTAR_LOCATION_NAME above for why this scraper cannot trust every
        event the feed carries)."""
        if not isinstance(location, dict):
            return False
        name = location.get("name")
        return isinstance(name, str) and name.strip().lower() == _NECTAR_LOCATION_NAME

    def _parse_jsonld_event(self, data: dict) -> Optional[ScrapedEvent]:
        """Convert a single schema.org Event dict into a ScrapedEvent, or None."""
        try:
            name = self._clean_text(data.get("name"))
            if not name:
                return None

            start = data.get("startDate") or ""
            if not start:
                return None
            parsed_dt = self._parse_start(start)
            if not parsed_dt:
                return None
            event_date = parsed_dt.date()
            # Midnight means no time was actually specified in the source data.
            show_time = parsed_dt.time() if (parsed_dt.hour, parsed_dt.minute) != (0, 0) else None

            url = data.get("url")
            url = url.strip() if isinstance(url, str) and url.strip() else None
            external_id = self._extract_external_id(url)

            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            ticket_url = offers.get("url") if isinstance(offers, dict) else None
            price_min = self._coerce_price(offers.get("price")) if isinstance(offers, dict) else None

            image_url = self.extract_schema_image(data.get("image"))
            description = self._clean_text(data.get("description"))

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="nectar",
                artist=name,
                show_time=show_time,
                ticket_url=ticket_url or url,
                price_min=price_min,
                image_url=image_url,
                description=description,
                external_id=external_id,
                source_url=url,
            )
        except Exception as e:
            logger.warning(f"[Nectar] Failed to parse event: {e}")
            return None

    @staticmethod
    def _parse_start(start: str) -> Optional[datetime]:
        """Parse a JSON-LD startDate (a genuine aware UTC instant) and convert
        it to Seattle local time (see the module-level _SEATTLE_TZ comment)."""
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(_SEATTLE_TZ)

    @staticmethod
    def _clean_text(raw) -> Optional[str]:
        """Unescape HTML entities and collapse the WordPress plugin's stray
        backslash-escapes (e.g. "Portland\\\\'s" -> "Portland's" once JSON
        decoding has already turned the doubled backslash into one) out of a
        JSON-LD text field. Returns None for anything that isn't a usable string."""
        if not isinstance(raw, str):
            return None
        from html import unescape
        text = unescape(raw).strip()
        # A backslash immediately before a quote/apostrophe is a WordPress
        # esc_js() artifact leaking into an already-JSON-decoded string, not a
        # real character in the venue's own text — collapse `\X` to `X`.
        text = re.sub(r"\\(.)", r"\1", text)
        return text or None

    @staticmethod
    def _extract_external_id(url: Optional[str]) -> Optional[str]:
        """The trailing numeric segment of a tixr.com event URL, or None."""
        if not url:
            return None
        match = _EXTERNAL_ID_RE.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _coerce_price(raw) -> Optional[float]:
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
