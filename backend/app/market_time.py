"""
Market-timezone calendar helpers.

Role: The single home for "what day is it?" decisions. All venues are in the
Research Triangle, so calendar-date logic (API date windows, the scrape diff's
one-miss-per-day cap) uses the Triangle's date — not the server's, which runs in
UTC in production and rolls over at 8 PM Eastern. API-layer code should keep
importing these via app.api.common (which re-exports them); the scrape manager
imports from here directly so the scraping layer never depends on the API layer.
Requires: stdlib only (zoneinfo).
"""

import zoneinfo
from datetime import date, datetime

TRIANGLE_TZ = zoneinfo.ZoneInfo("America/New_York")


def today_in_triangle() -> date:
    """Current calendar date in the venues' market timezone (America/New_York)."""
    return datetime.now(TRIANGLE_TZ).date()
