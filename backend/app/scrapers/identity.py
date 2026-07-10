"""
Stable event identity helpers (issue #8).

Role: Defines how a scraped event maps to a durable identity: URL normalization
(host-independent, tracking-param-free) and the tier-prefixed source_key that
external consumers key on. Used by the scrape manager for reconciliation and by
the Alembic backfill. Pure stdlib — no database, no HTTP.

The normalized-URL form and the source_key prefixes are an external contract
(Backend-Service keys its concerts table on source_key); change them only with
a documented migration plan.
"""
import enum
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit


class UrlIdentityVerdict(enum.Enum):
    """Per-scraper audit verdict: may source_url serve as event identity?

    TRUSTED asserts BOTH properties for every URL the scraper emits:
    rename/reschedule stability (the source keeps the URL when the event is
    edited) and occurrence-uniqueness (one URL never covers two event-dates).
    Anything less is HASH_FALLBACK — the scraper's events reconcile by external
    id when present, else content hash, and source_url is never an identity key.
    """

    TRUSTED = "trusted"
    HASH_FALLBACK = "hash_fallback"

# Query parameters that carry analytics state, not event identity. Stripped so a
# link shared through a campaign normalizes to the same identity as the bare URL.
_TRACKING_PARAMS = ("fbclid", "gclid")
_TRACKING_PREFIXES = ("utm_",)


def _is_tracking_param(key: str) -> bool:
    return key in _TRACKING_PARAMS or key.startswith(_TRACKING_PREFIXES)


def normalize_source_url(url: Optional[str]) -> Optional[str]:
    """Reduce a source_url to its identity-bearing core: path + non-tracking query.

    Scheme, host, and fragment are stripped (CDN/domain changes must not change
    identity); the query string is kept because ticketing pages may carry event
    identity in a parameter; a trailing slash is insignificant. Returns None for
    missing/blank input — no URL means no URL-tier identity.
    """
    if url is None or not url.strip():
        return None
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    # Sorted so the SAME page emitting its params in a different order still
    # normalizes to one identity — source_key stability outranks preserving the
    # original query-string form.
    query_pairs = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking_param(k)
    )
    if query_pairs:
        return f"{path}?{urlencode(query_pairs)}"
    return path


def derive_source_key(
    external_id: Optional[str],
    normalized_url: Optional[str],
    content_hash: str,
    verdict: UrlIdentityVerdict,
) -> str:
    """Derive the tier-prefixed source_key: ext: > url: > hash:.

    The prefix names the winning tier so consumers can read the stability class
    (ext/url keys survive renames and reschedules; hash keys do not). The url
    tier applies only when the scraper's audit verdict is TRUSTED.
    """
    if external_id:
        return f"ext:{external_id}"
    if normalized_url and verdict is UrlIdentityVerdict.TRUSTED:
        return f"url:{normalized_url}"
    return f"hash:{content_hash}"


# --- Scraper registry + audit verdicts -------------------------------------
#
# The canonical scraper_type → class mapping. The scrape manager dispatches
# through it, and url_identity_verdict() gates URL-tier reconciliation, the
# Alembic source_key population, and the duplicate backfill. Import paths are
# stored as strings so this module stays stdlib-only and scraper dependencies
# (httpx, bs4) load lazily.

SCRAPER_REGISTRY: dict[str, tuple[str, str]] = {
    "ticketmaster": ("app.scrapers.ticketmaster", "TicketmasterScraper"),
    "rhp_events": ("app.scrapers.rhp_events", "RHPEventsScraper"),
    "tribe_events": ("app.scrapers.tribe_events", "TribeEventsScraper"),
    "squarespace": ("app.scrapers.squarespace", "SquarespaceScraper"),
    "eventprime": ("app.scrapers.eventprime", "EventPrimeScraper"),
    "motorco": ("app.scrapers.motorco", "MotorcoScraper"),
    "carolina_theatre": ("app.scrapers.carolina_theatre", "CarolinaTheatreScraper"),
    "venuepilot": ("app.scrapers.venuepilot", "VenuePilotScraper"),
    "koka_booth": ("app.scrapers.koka_booth", "KokaBoothScraper"),
    "mec": ("app.scrapers.mec", "MECScraper"),
    "webflow_cms": ("app.scrapers.webflow_cms", "WebflowCMSScraper"),
    "tickpick_organizer": ("app.scrapers.tickpick_organizer", "TickPickOrganizerScraper"),
}


def scraper_class(scraper_type: str):
    """Resolve a scraper_type to its class, or None for unknown types."""
    import importlib

    entry = SCRAPER_REGISTRY.get(scraper_type)
    if entry is None:
        return None
    module_path, class_name = entry
    return getattr(importlib.import_module(module_path), class_name)


def url_identity_verdict(scraper_type: str) -> UrlIdentityVerdict:
    """The audit verdict for a scraper_type; unknown types get the safe fallback.

    HASH_FALLBACK for unknown types means a future scraper added without an
    audit reverts to content-hash identity (today's behavior) rather than
    trusting URLs it never earned.
    """
    cls = scraper_class(scraper_type)
    if cls is None:
        return UrlIdentityVerdict.HASH_FALLBACK
    return getattr(cls, "URL_IDENTITY", UrlIdentityVerdict.HASH_FALLBACK)
