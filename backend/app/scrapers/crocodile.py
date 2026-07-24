"""Bespoke scraper for The Crocodile (Seattle), which aggregates its own main
room, Madame Lou's, and back bar into one calendar at calendar.thecrocodile.com
— a Webflow-hosted CMS collection list, not a JSON-LD feed.

Role: One of many venue scrapers run in parallel by scrapers/manager.py, which is
triggered every 6 hours via POST /api/scrape (called by the scheduler or Cloud
Scheduler). The Crocodile sells through roughly six different outbound ticketers
(TicketWeb, DICE, Humanitix, VenuePilot, Tixr, AXS, Ticketmaster) with no single
platform token, so no existing platform scraper (ticketmaster.py, venuepilot.py,
etc.) can cover it alone — this scraper reads the venue's own aggregated calendar
instead and follows each listing's own links for both identity and the ticket URL.
Requires: httpx (via BaseScraper.fetch_soup), app.scrapers.base (BaseScraper,
ScrapedEvent), app.scrapers.identity (UrlIdentityVerdict).
"""

# --- Imports ---

import logging
from datetime import date, datetime
from typing import Optional

from bs4 import Tag

from app.scrapers.base import BaseScraper, ScrapedEvent
from app.scrapers.identity import UrlIdentityVerdict

# --- Module-level setup ---

logger = logging.getLogger(__name__)

# strptime formats tried against the internal /shows/ link's date text, which is
# always abbreviated-month ("Jul 25, 2026") and carries a time ("Jul 25, 2026
# 6:00 PM") whenever the venue has one to show — checked first so a present time
# is never silently dropped.
_DATE_FORMATS = ("%b %d, %Y %I:%M %p", "%b %d, %Y")


# --- Scraper class ---

class CrocodileScraper(BaseScraper):
    """Scrape The Crocodile's own aggregated calendar (calendar.thecrocodile.com).

    Each listing (``div.uui-layout88_item``) renders two ``a.link-block-2``
    wrappers around identical name/date/image content, always in this order:

    1. The outbound ticket link — the real ticketer URL when one is set, or a
       Webflow-conditional placeholder (``href="#"``, class carries
       ``w-condition-invisible``) when it isn't. Its date text is always
       date-only (no time).
    2. The venue's own detail page (``/shows/<slug>``) — always present and
       always the visible link when (1) is a placeholder. Its date text carries
       a time whenever the show has one.

    A live fetch on 2026-07-24 showed "SOS: The Recession Pop Party" rendering
    at slug ``/shows/sos-the-recession-pop-party-18-jul`` while its displayed
    date was July 25 — the slug does not track the event's date field, i.e. it
    is reschedule-stable (see the URL_IDENTITY audit below).

    No room field (main room / Madame Lou's / back bar) appears anywhere in this
    markup — not on the listing cards, and a fetched detail page (`/shows/...`)
    carries none either. Ticket-URL text occasionally names a room ("madame-lous"
    appeared in two outbound TicketWeb slugs during the same fetch), but that is
    an artifact of the outbound ticketer's own slug, not a field the Crocodile's
    own page structure exposes — too unreliable to parse as a room label. All
    three rooms are therefore modeled as one venue until the site starts
    surfacing room data structurally.

    Used by: The Crocodile (Seattle)
    """

    # Audit (issue #75): source_url is the venue's own /shows/<slug> detail-page
    # URL (the second link on every listing), never the outbound ticketer link.
    # That own URL is always present (unlike the outbound link, absent on ~1/3
    # of listings in the 2026-07-24 fetch) and, per Webflow CMS collection
    # behavior, its slug is assigned once per item and independent of the
    # title/date fields it renders — a rename or reschedule edits those fields,
    # not the slug, and the "SOS" example above is live evidence the slug can
    # already disagree with the displayed date without changing. Webflow also
    # enforces per-collection slug uniqueness, so it can't alias two occurrences
    # together. The outbound ticketer link, by contrast, spans ~6 heterogeneous
    # platforms with no uniform stability guarantee and is sometimes absent
    # entirely — not auditable as TRUSTED.
    URL_IDENTITY = UrlIdentityVerdict.TRUSTED

    async def scrape(self) -> list[ScrapedEvent]:
        """Fetch the Crocodile's calendar page and return upcoming ScrapedEvent objects."""
        url = self.config.get("url", "https://calendar.thecrocodile.com/")
        today = date.today()

        soup = await self.fetch_soup(url)

        events = []
        for item in soup.select("div.uui-layout88_item"):
            try:
                parsed = self._parse_item(item, today)
            except Exception as e:
                # Per-listing error isolation: one malformed card can't kill the
                # rest of the venue's scrape cycle.
                logger.warning(f"[Crocodile] Listing parse error: {e}")
                continue
            if parsed:
                events.append(parsed)

        # Deduplicate by hash in case the listing renders the same show twice.
        seen = set()
        unique = []
        for ev in events:
            if ev.hash not in seen:
                seen.add(ev.hash)
                unique.append(ev)

        logger.info(f"[Crocodile] Found {len(unique)} upcoming events for {self.venue_slug}")
        return unique

    def _parse_item(self, item: Tag, today: date) -> Optional[ScrapedEvent]:
        """Parse a single listing card into a ScrapedEvent, or None on failure/past date."""
        links = item.select("a.link-block-2")
        if len(links) != 2:
            # Structure drift — neither expected link is present.
            return None
        outbound_link, detail_link = links

        detail_href = detail_link.get("href", "")
        if not detail_href:
            return None
        # The site emits relative hrefs ("/shows/<slug>"); resolve against the
        # calendar's own host so source_url is a fully-qualified, stable URL.
        source_url = f"https://calendar.thecrocodile.com{detail_href}"

        name_el = item.select_one("h3.uui-heading-xxsmall-2")
        if not name_el:
            return None
        name = name_el.get_text(strip=True)
        if not name:
            return None

        # Scoped to detail_link (not the whole item): the outbound link's date
        # text is always date-only, so reading item-wide would silently drop
        # every show's time. See the class docstring for the two-link contract.
        date_el = detail_link.select_one(".cal-start-date")
        if not date_el:
            return None
        parsed_dt = self._parse_date_text(date_el.get_text(strip=True))
        if not parsed_dt:
            return None
        event_date = parsed_dt.date()
        if event_date < today:
            return None  # Skip past events

        # Only record show_time if a non-midnight time was actually specified —
        # a bare date ("Sep 29, 2026") parses to midnight with no way to tell
        # that apart from a genuine midnight show, so treat midnight as "unknown".
        show_time = parsed_dt.time() if (parsed_dt.hour, parsed_dt.minute) != (0, 0) else None

        image_el = item.select_one("img.image-40")
        image_url = image_el.get("src") if image_el else None

        # The outbound link is a real ticketer URL unless it's the Webflow
        # placeholder ("#") rendered when no outbound ticket is set yet.
        outbound_href = outbound_link.get("href", "")
        ticket_url = outbound_href if outbound_href and outbound_href != "#" else source_url

        # A sold-out tag is only actually visible (not Webflow-conditional
        # w-condition-invisible) when the show is genuinely sold out.
        tag_el = item.select_one("img.event-tag")
        status = "sold_out" if tag_el and "w-condition-invisible" not in tag_el.get("class", []) else "on_sale"

        return ScrapedEvent(
            name=name,
            date=event_date,
            venue_slug=self.venue_slug,
            source="crocodile",
            artist=name,
            show_time=show_time,
            ticket_url=ticket_url,
            image_url=image_url or None,
            source_url=source_url,
            status=status,
        )

    @staticmethod
    def _parse_date_text(text: str) -> Optional[datetime]:
        """Parse the detail link's date text ("Jul 25, 2026" or "Jul 25, 2026 6:00 PM")."""
        if not text:
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None
