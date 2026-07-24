"""
AEG venue-site scraper — parses events from the AEG Presents Seattle venue
sites' own native HTML listings (issue #68).

Role: One of many venue scrapers invoked by scrapers/manager.py during each
scrape cycle. The Showbox, Showbox SoDo, Neumos, and Barboza (all AEG
Presents Seattle rooms) sell tickets through axs.com, but axs.com is
Cloudflare-walled from datacenter IPs — a plain httpx GET gets a TLS-
fingerprint reset, and even a paid ScrapingBee stealth proxy only clears it
at ~$49/mo (2026-07-24 recon). The venue sites themselves, however, are
ungated plain HTML (confirmed HTTP 200 via plain httpx the same day) and each
listing row embeds the same axs.com ticket URL this scraper would otherwise
have had to scrape axs.com directly to get — so this scrapes the venue sites
instead:
  - Showbox + Showbox SoDo -> https://www.showboxpresents.com/events/all
    (one shared "all AEG venues" listing; filtered down to the two Showbox
    rooms via the ``venue_name`` config key against each card's own
    "@ <venue>" label — this single page also lists Portland/Bozeman/etc AEG
    rooms this scraper is not responsible for).
  - Neumos + Barboza -> https://www.neumos.com/events (one page; Barboza has
    no site of its own and rides this page via the ticket link's own
    ``?skin=barboza`` vs ``?skin=neumos`` query param, filtered via the
    ``skin`` config key).
Both page's cards embed an axs.com/events/<numeric-id>...?skin=<venue>
ticket URL — the numeric id is the ext: identity (see URL_IDENTITY below).
Requires: app.scrapers.base (BaseScraper, ScrapedEvent) for the shared
fetch_soup HTTP path; venue config must include ``url`` plus exactly one of
``venue_name`` (Showbox-style pages) or ``skin`` (Neumos-style pages) — see
each config's docstring on the class below.
"""

# --- Imports ---

import logging
import re
from datetime import date, datetime, time
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from bs4 import Tag

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---

logger = logging.getLogger(__name__)

# The numeric AXS event id embedded in every ticket link, e.g.
# https://www.axs.com/events/1464405/earlybirds-club-tickets?skin=showboxpresents
# -> "1464405". This id is the ext: identity (see URL_IDENTITY).
_EXTERNAL_ID_RE = re.compile(r"axs\.com/events/(\d+)")

# A "H:MM AM/PM" clock time embedded in free text ("Show\n\t\t\t\t\t6:00 PM",
# "Doors: 9:00 PM"). Both venue sites' time fields are label + time smashed
# together with irregular whitespace, so extraction is regex-first rather
# than a fixed strptime format.
_TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)

# showboxpresents.com's span.date renders "Fri, Jul 24, 2026" (or the literal
# string "TBD" when the show has been postponed with no new date yet — see
# the "Riot Ten" fixture card).
_SHOWBOX_DATE_FMT = "%a, %b %d, %Y"

# neumos.com's div.date[aria-label] renders "July 24 2026" — note the site
# pads single-digit days with an extra space ("August  1 2026"), collapsed
# by re.sub before parsing.
_NEUMOS_DATE_FMT = "%B %d %Y"


# --- Scraper class ---

class AEGVenueScraper(BaseScraper):
    """Scrape an AEG Presents Seattle venue site's own event listing.

    Both source pages render event cards as ``div.entry`` with a
    ``div.buttons a[href*="axs.com/events/"]`` ticket link and an
    ``h3 a`` title/detail link — the two structural features every card on
    either page shares, and this scraper's only site-agnostic assumptions.
    Everything else (date/time markup, the venue-vs-skin filter) differs by
    page and is dispatched on which filter key ``scraper_config`` sets:

    - Showbox-style (``scraper_config = {"url": "https://www.showboxpresents.com/events/all", "venue_name": "The Showbox"}``
      or ``"venue_name": "Showbox SoDo"``): cards carry ``span.date``/
      ``span.time``/``span.venue``; ``venue_name`` is matched against the
      card's own "@ <venue>" label (the page lists AEG's whole Seattle/
      Portland/Bozeman network, not just the two Showbox rooms).
    - Neumos-style (``scraper_config = {"url": "https://www.neumos.com/events", "skin": "neumos"}``
      or ``"skin": "barboza"``): cards carry ``div.date[aria-label]`` and a
      ``div.meta div.time`` "Doors: H:MM PM" string; ``skin`` is matched
      against the ticket link's own ``?skin=`` query param (Barboza has no
      site of its own and rides this same page via that param).

    Used by: The Showbox, Showbox SoDo, Neumos, Barboza (Seattle).
    """

    # Audit (issue #68): source_url is each card's own venue-site detail page
    # (showboxpresents.com/events/detail/<id> or neumos.com/events/detail/
    # <slug>-<id>), never the shared listing page and never the outbound
    # axs.com link. Both venue sites key that detail URL on the same numeric
    # id this scraper also extracts as external_id, which is a promising sign
    # for rename/reschedule stability — but that's inferred from URL shape,
    # not observed: this is a single snapshot with no reschedule/rename event
    # to compare against (mirrors ticketweb.py's reasoning for an analogous
    # numeric-id-in-URL case). Every card observed here carries a ticket link
    # and therefore an external_id, so this scraper's real-world identity is
    # already ext:-tier regardless of this verdict; HASH_FALLBACK is the safe
    # choice for the rare card that somehow lacks one.
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the configured venue-site listing page and return upcoming events."""
        url = self.config.get("url")
        if not url:
            raise ValueError(f"No url configured for {self.venue_slug}")
        venue_name = self.config.get("venue_name")
        skin = self.config.get("skin")
        if not venue_name and not skin:
            raise ValueError(
                f"AEGVenueScraper for {self.venue_slug} needs venue_name or skin in scraper_config"
            )

        soup = await self.fetch_soup(url)
        today = date.today()

        events = []
        for item in soup.select("div.entry"):
            try:
                parsed = self._parse_item(item, today, venue_name, skin)
            except Exception as e:
                # Per-listing error isolation: one malformed card can't kill the
                # rest of the venue's scrape cycle.
                logger.warning(f"[AEGVenue] Listing parse error: {e}")
                continue
            if parsed:
                events.append(parsed)

        logger.info(f"[AEGVenue] Found {len(events)} upcoming events for {self.venue_slug}")
        return events

    def _parse_item(
        self, item: Tag, today: date, venue_name: Optional[str], skin: Optional[str]
    ) -> Optional[ScrapedEvent]:
        """Parse a single ``div.entry`` card into a ScrapedEvent, or None on
        failure/wrong-venue/wrong-skin/unparseable-date/past date."""
        ticket_link = item.select_one('div.buttons a[href*="axs.com/events/"]')
        if not ticket_link:
            return None
        ticket_url = ticket_link.get("href", "")
        id_match = _EXTERNAL_ID_RE.search(ticket_url)
        if not id_match:
            return None
        external_id = id_match.group(1)

        if skin is not None:
            qs_skin = parse_qs(urlsplit(ticket_url).query).get("skin", [None])[0]
            if qs_skin != skin:
                return None

        title_el = item.select_one("h3 a")
        if not title_el:
            return None
        name = title_el.get_text(strip=True)
        if not name:
            return None
        source_url = title_el.get("href") or None

        if venue_name is not None:
            # Showbox-style-only field: a Neumos-style card (no span.venue at
            # all) can never match a venue_name filter and is correctly skipped.
            venue_el = item.select_one("span.venue")
            if venue_el is None:
                return None
            venue_text = venue_el.get_text(strip=True).lstrip("@").strip()
            if venue_text != venue_name:
                return None

        event_date, show_time = self._parse_date_time(item)
        if event_date is None:
            return None
        if event_date < today:
            return None  # Skip past events

        image_el = item.select_one("div.thumb img")
        image_url = image_el.get("src") if image_el else None

        return ScrapedEvent(
            name=name,
            date=event_date,
            venue_slug=self.venue_slug,
            source="aeg_venue",
            external_id=external_id,
            artist=name,
            show_time=show_time,
            ticket_url=ticket_url,
            image_url=image_url or None,
            source_url=source_url,
        )

    @staticmethod
    def _parse_date_time(item: Tag) -> tuple[Optional[date], Optional[time]]:
        """Parse the card's date/time, dispatching on which markup shape is present.

        Neumos-style cards carry ``div.date[aria-label]``; Showbox-style cards
        carry ``span.date``/``span.time`` instead. Returns ``(None, None)`` when
        the date is missing or unparseable (e.g. the literal "TBD" a postponed
        Showbox show renders instead of a date).
        """
        neumos_date_el = item.select_one("div.date[aria-label]")
        if neumos_date_el is not None:
            aria = re.sub(r"\s+", " ", neumos_date_el.get("aria-label", "")).strip()
            try:
                event_date = datetime.strptime(aria, _NEUMOS_DATE_FMT).date()
            except ValueError:
                return None, None
            time_el = item.select_one("div.meta div.time")
            show_time = None
            if time_el:
                match = _TIME_RE.search(time_el.get_text(" ", strip=True))
                if match:
                    show_time = datetime.strptime(match.group(1).upper(), "%I:%M %p").time()
            return event_date, show_time

        showbox_date_el = item.select_one("span.date")
        if showbox_date_el is None:
            return None, None
        date_text = showbox_date_el.get_text(strip=True)
        try:
            event_date = datetime.strptime(date_text, _SHOWBOX_DATE_FMT).date()
        except ValueError:
            return None, None
        time_el = item.select_one("span.time")
        show_time = None
        if time_el:
            match = _TIME_RE.search(time_el.get_text(" ", strip=True))
            if match:
                parsed_time = datetime.strptime(match.group(1).upper(), "%I:%M %p").time()
                # A bare date with no real time renders no span.time at all on
                # this site (unlike Crocodile's midnight-default quirk), so any
                # parsed time here is a genuine one — no midnight special-case
                # needed.
                show_time = parsed_time
        return event_date, show_time
