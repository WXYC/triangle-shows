"""
Abstract base class and shared data structures for all venue scrapers.

Role: Defines the ScrapedEvent dataclass (the unit of data each scraper returns)
and BaseScraper ABC (the interface every venue scraper must implement). The scrape
manager (scrapers/manager.py) imports BaseScraper subclasses, calls their scrape()
method, and uses ScrapedEvent.hash for deduplication before upserting to PostgreSQL.
Requires: No env vars or external services — pure Python stdlib only.
"""
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, time, datetime
from functools import cached_property
from typing import Optional

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


# --- Shared HTTP Headers ---

# Mimic a real browser so venue sites don't block the scraper as a bot
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
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

    # Default strptime formats tried by parse_date, in precedence order. The
    # union of what the HTML scrapers (rhp_events, eventprime, koka_booth,
    # webflow_cms) needed before this was consolidated. Formats that carry a
    # %Y are tried first; the year-less pair is applied last with the
    # current-year / roll-forward rule (see parse_date).
    _DATE_FORMATS_WITH_YEAR = (
        "%B %d, %Y",       # January 15, 2025
        "%b %d, %Y",       # Jan 15, 2025
        "%m/%d/%Y",        # 01/15/2025
        "%m-%d-%Y",        # 01-15-2025
        "%Y-%m-%d",        # 2025-01-15
        "%A, %B %d, %Y",   # Wednesday, January 15, 2025
        "%a, %b %d, %Y",   # Wed, Jan 15, 2025
    )
    _DATE_FORMATS_NO_YEAR = (
        "%B %d",           # January 15
        "%b %d",           # Jan 15
    )

    @staticmethod
    def parse_date(text: str, formats: Optional[list[str]] = None) -> Optional[date]:
        """Parse a date from flexible listing-page text.

        Consolidates the date-text parsing previously duplicated across the HTML
        scrapers. The steps, in order:

        1. Strip a leading weekday name — abbreviated or full ("Fri, ",
           "Saturday, ", "Thursday, ").
        2. Strip ordinal suffixes on the day number ("26th" -> "26").
        3. Walk ``formats`` (defaults to the class format list) and return the
           first successful ``strptime``.
        4. For a format that carries no year, assume the current year and roll
           forward to next year when the resulting date is already well past
           (more than a week ago) — so a listing that omits the year doesn't
           back-date an upcoming show.

        Args:
            text: Raw date text from the page (may be None/blank).
            formats: Optional explicit strptime format list. When given, it
                replaces the default with-year list; a format is treated as
                year-less (and gets the roll-forward rule) when it has no
                ``%Y``/``%y`` token. When omitted, the default with-year and
                year-less lists are both tried.

        Returns:
            A ``date``, or ``None`` if nothing parsed.
        """
        if not text:
            return None

        # Strip a leading weekday name. "(Mon|Tue|...)\w*" matches both the
        # 3-letter abbreviation and the full name (e.g. "Mon" and "Monday").
        text = re.sub(
            r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s*', '', text.strip(), flags=re.IGNORECASE
        )
        # Strip ordinal suffixes on the day number ("1st"/"2nd"/"3rd"/"26th").
        text = re.sub(r'(\d+)(st|nd|rd|th)\b', r'\1', text, flags=re.IGNORECASE)
        text = text.strip().rstrip(',').strip()
        if not text:
            return None

        if formats is not None:
            with_year = list(formats)
            # A caller-supplied format is year-less only if it has no year token.
            no_year = [f for f in formats if '%Y' not in f and '%y' not in f]
            with_year = [f for f in formats if f not in no_year]
        else:
            with_year = list(BaseScraper._DATE_FORMATS_WITH_YEAR)
            no_year = list(BaseScraper._DATE_FORMATS_NO_YEAR)

        for fmt in with_year:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue

        for fmt in no_year:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            today = date.today()
            candidate = parsed.replace(year=today.year).date()
            # Roll forward when the current-year guess is well past (more than a
            # week ago) — the next future occurrence is the intended date.
            if (candidate - today).days < -7:
                candidate = candidate.replace(year=today.year + 1)
            return candidate

        return None

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize event/artist name for comparison."""
        return re.sub(r'\s+', ' ', name.strip())
