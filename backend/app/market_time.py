"""
Market-timezone calendar helpers.

Role: The single home for "what day is it?" decisions. Calendar-date logic (API
date windows, the scrape diff's one-miss-per-day cap) uses the configured
region's market date — not the server's, which runs in UTC in production and
rolls over mid-evening Eastern for Triangle. API-layer code should keep
importing these via app.api.common (which re-exports them); the scrape manager
imports from here directly so the scraping layer never depends on the API layer.

The zone comes from the active region's site.toml (region-pack epic, issue #62;
this module's rename from TRIANGLE_TZ/today_in_triangle is Phase 2, issue #64)
via app.site_config's lazy, resettable singleton. market_tz() is a function
(not a module-level constant, as TRIANGLE_TZ was) because the zone depends on
config that itself loads lazily on first access — function-level lookup with a
module-level cache, so this module stays dependency-light (imported by both the
scrape manager and the API layer) and so the shared conftest reset fixture
(tests/conftest.py) covers this cache alongside app.site_config's.
Requires: stdlib (zoneinfo) + app.site_config.
"""

import zoneinfo
from datetime import date, datetime
from typing import Optional

from app.site_config import load_site_config

_market_tz_cache: Optional[zoneinfo.ZoneInfo] = None


def market_tz() -> zoneinfo.ZoneInfo:
    """The active region's market timezone, resolved from site.toml and cached."""
    global _market_tz_cache
    if _market_tz_cache is None:
        _market_tz_cache = zoneinfo.ZoneInfo(load_site_config().site.timezone)
    return _market_tz_cache


def today_in_market() -> date:
    """Current calendar date in the active region's market timezone."""
    return datetime.now(market_tz()).date()


def reset_market_tz_cache() -> None:
    """Drop the cached zone so the next market_tz() call re-reads site config.

    Wired into the same conftest fixture that resets app.site_config's caches, so
    a test pointing SITE_CONFIG_PATH at a fixture pack doesn't see a previous
    test's cached zone.
    """
    global _market_tz_cache
    _market_tz_cache = None
