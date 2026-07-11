"""
Abstract base class and shared data structures for all venue scrapers.

Role: Defines the ScrapedEvent dataclass (the unit of data each scraper returns)
and BaseScraper ABC (the interface every venue scraper must implement). The scrape
manager (scrapers/manager.py) imports BaseScraper subclasses, calls their scrape()
method, and uses ScrapedEvent.hash for deduplication before upserting to PostgreSQL.
BaseScraper also owns the shared HTML fetch path (fetch_soup) so the HTML scrapers
don't each re-declare the httpx client config and BeautifulSoup parser.
Requires: httpx and beautifulsoup4/lxml (for fetch_soup); no env vars or external
services.
"""
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, time, datetime
from functools import cached_property
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.scrapers.identity import UrlIdentityVerdict


# --- ScrapedEvent Dataclass ---

@dataclass
class ScrapedEvent:
    # Required fields — every scraper must supply these
    name: str
    date: date
    venue_slug: str
    source: str
    # Optional fields — scrapers fill in what their venue page exposes
    external_id: Optional[str] = None
    artist: Optional[str] = None
    # Structured clean performer from the source (schema.org Event.performer,
    # Ticketmaster attractions[0]) when the scraper has one — NOT a copy of the
    # billing string. The scrape manager derives the stored Event.headliner by
    # running this (or, when None, the name) through headliner.extract_headliner.
    headliner: Optional[str] = None
    support_artists: Optional[str] = None
    doors_time: Optional[time] = None
    show_time: Optional[time] = None
    ticket_url: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    image_url: Optional[str] = None
    genre: Optional[str] = None
    subgenre: Optional[str] = None
    status: str = "on_sale"
    age_restriction: Optional[str] = None
    description: Optional[str] = None
    source_url: Optional[str] = None

    def __post_init__(self):
        # A blank external_id must never survive as an identity key: reconciliation
        # would match every event at the venue onto one row (issue #8). Non-string
        # ids (numeric JSON API values) are stringified rather than crashing the
        # per-event parse — a silent drop of every event at a venue is worse than
        # a lenient cast.
        if self.external_id is not None:
            self.external_id = str(self.external_id).strip() or None
        # Malformed JSON-LD can hand scrapers a dict/list where a URL string
        # belongs ("url": {"@id": ...}). A non-string is not a usable identity
        # and would fail the varchar column bind — treat it as no URL.
        if self.source_url is not None and not isinstance(self.source_url, str):
            self.source_url = None

    @cached_property
    def hash(self) -> str:
        """Generate dedup hash from venue_slug + date + normalized name.

        Strip the words 'box'/'boxes' before normalizing so DPAC events
        like 'Piano Box Series' and 'Piano Boxes Series' collapse to one hash.

        Cached: the upsert and the snapshot diff each read it several times per
        event. Scrapers must not mutate the identity fields (name, date,
        venue_slug) after the first access.
        """
        name = re.sub(r'\b(box|boxes)\b', '', self.name, flags=re.IGNORECASE)
        normalized = re.sub(r'[^a-z0-9]', '', name.lower().strip())
        raw = f"{self.venue_slug}|{self.date.isoformat()}|{normalized}"
        return hashlib.sha256(raw.encode()).hexdigest()


# --- Shared HTTP config ---

# Seconds before an HTTP request is abandoned. Shared so every scraper's fetch
# uses the same budget (a slow venue can't hang a whole scrape cycle).
HTTP_TIMEOUT = 30

# Mimic a real browser so venue sites don't block the scraper as a bot. Keep the
# User-Agent on a current Chrome release: a stale UA is a fingerprint some venue
# WAFs hard-block (a stale Chrome/122 was the root cause of the Carolina Theatre
# outage), so bump this when Chrome's stable major moves on. A scraper that needs
# its own UA can pass headers= to fetch_soup rather than editing this shared dict.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# --- BaseScraper ABC ---

class BaseScraper(ABC):
    """Abstract base class for all venue scrapers."""

    # Identity audit verdict (issue #8): may this scraper's source_url serve as
    # event identity? The safe default is HASH_FALLBACK; every concrete scraper
    # must still declare its own verdict explicitly — tests/test_identity.py
    # rejects inherited declarations, so this default only defends runtime paths
    # (e.g. a scraper deployed without its audit).
    URL_IDENTITY = UrlIdentityVerdict.HASH_FALLBACK

    def __init__(self, venue_slug: str, config: Optional[dict] = None):
        self.venue_slug = venue_slug
        # config allows per-venue overrides (e.g. custom URLs, feature flags)
        self.config = config or {}

    @abstractmethod
    async def scrape(self) -> list[ScrapedEvent]:
        """Scrape events and return a list of ScrapedEvent objects."""
        ...

    # --- HTTP Helpers ---
    # Shared fetch path so the HTML scrapers don't each repeat the httpx client
    # config + GET + raise_for_status + BeautifulSoup(..., "lxml") boilerplate.

    @staticmethod
    def http_client(headers: Optional[dict] = None) -> httpx.AsyncClient:
        """Build the standard browser-mimicking httpx client (caller manages its lifecycle).

        Multi-page scrapers that fetch detail pages open one of these with
        ``async with self.http_client() as client:`` and pass ``client`` to each
        ``fetch_soup`` call so a single connection pool is reused. Single-page
        scrapers don't need this — ``fetch_soup`` opens and closes its own client.
        Pass ``headers`` to override the shared browser headers (e.g. a venue that
        needs a bespoke User-Agent); ``None`` uses :data:`BROWSER_HEADERS`.
        """
        return httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers=headers if headers is not None else BROWSER_HEADERS,
        )

    async def fetch_soup(
        self,
        url: str,
        *,
        client: Optional[httpx.AsyncClient] = None,
        headers: Optional[dict] = None,
        parser: str = "lxml",
    ) -> BeautifulSoup:
        """GET ``url`` and return its parsed BeautifulSoup tree.

        Encapsulates the client config (timeout, redirects, browser headers), the
        GET, ``raise_for_status()``, and the BeautifulSoup parse that every HTML
        scraper otherwise repeats. Raises ``httpx.HTTPStatusError`` on a non-2xx
        response — a caller that treats some statuses as normal (e.g. a 404 that
        just means "past the last page") should catch it.

        Pass ``client`` to reuse an existing connection pool for extra fetches
        (multi-page scrapers); omit it and ``fetch_soup`` opens and closes its own
        client for the call. ``headers`` overrides the request headers: on an
        owned client they replace the browser defaults for the whole client; on a
        passed-in ``client`` they are per-request headers layered over whatever
        headers that client already carries.
        """
        if client is not None:
            resp = await client.get(url, headers=headers) if headers is not None else await client.get(url)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, parser)

        async with self.http_client(headers=headers) as owned_client:
            resp = await owned_client.get(url)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, parser)

    # --- Parsing Helpers ---
    # Shared utility methods so individual scrapers don't duplicate price/time logic

    @staticmethod
    def parse_price(text: str) -> Optional[float]:
        """Extract a numeric price from text like '$15.00', 'Free', '$20'."""
        if not text:
            return None
        text = text.strip().lower()
        if text in ("free", "free!", "no cover", "$0", "$0.00"):
            return 0.0
        match = re.search(r'\$?\s*(\d+(?:\.\d{2})?)', text)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def parse_price_range(text: str) -> tuple[Optional[float], Optional[float]]:
        """Parse price range like '$15-$25', '$20', 'Free'."""
        if not text:
            return None, None
        text = text.strip().lower()
        if text in ("free", "free!", "no cover"):
            return 0.0, 0.0
        prices = re.findall(r'\$?\s*(\d+(?:\.\d{2})?)', text)
        if len(prices) >= 2:
            return float(prices[0]), float(prices[1])
        elif len(prices) == 1:
            # Single price — treat it as both min and max
            p = float(prices[0])
            return p, p
        return None, None

    @staticmethod
    def parse_time(text: str) -> Optional[time]:
        """Parse time from various formats: '7 pm', '8:30 PM', '19:00', '7pm'."""
        if not text:
            return None
        text = text.strip().lower().replace('.', '')
        # Try HH:MM AM/PM
        match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)', text)
        if match:
            h, m, ampm = int(match.group(1)), int(match.group(2)), match.group(3)
            if ampm == 'pm' and h != 12:
                h += 12
            elif ampm == 'am' and h == 12:
                h = 0
            return time(h, m)
        # Try H AM/PM (no minutes)
        match = re.search(r'(\d{1,2})\s*(am|pm)', text)
        if match:
            h, ampm = int(match.group(1)), match.group(2)
            if ampm == 'pm' and h != 12:
                h += 12
            elif ampm == 'am' and h == 12:
                h = 0
            return time(h, 0)
        # Try 24-hour HH:MM
        match = re.search(r'(\d{1,2}):(\d{2})', text)
        if match:
            h, m = int(match.group(1)), int(match.group(2))
            if 0 <= h <= 23 and 0 <= m <= 59:
                return time(h, m)
        return None

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize event/artist name for comparison."""
        return re.sub(r'\s+', ' ', name.strip())
