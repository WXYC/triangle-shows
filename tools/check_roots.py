"""
Verifies that scraper root domain URLs resolve correctly and sniffs for event-related links.

Role: Developer utility — run manually when adding or debugging a venue scraper to confirm
the venue's homepage is reachable and to discover likely event/calendar page URLs.
Not part of the runtime; never called by the scheduler or main.py.

Requires: No env vars or external modules beyond the stdlib. Internet access required.
"""

# --- Imports ---
import urllib.request
import urllib.error
import re

# --- Config ---

# Spoof a real browser User-Agent to avoid bot-detection blocks on venue sites.
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# Venue slugs paired with their root homepage URLs.
# Add entries here when onboarding a new venue to quickly verify reachability.
roots = [
    ("motorco",   "https://www.motorcomusic.com/"),
    ("kings",     "https://www.kingsbarcade.com/"),
    ("the-cave",  "https://www.caverntavern.com/"),
    ("moon-room", "https://www.moonroomraleigh.com/"),
    ("neptunes",  "https://www.neptunesparlour.com/"),
]

# --- Main: probe each root URL ---

for name, url in roots:
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            final_url = r.url
            status = r.status
            html = r.read().decode("utf-8", errors="ignore")
            # Extract all href values, then filter to ones that look like event/calendar pages.
            links = set(re.findall(r'href="([^"]*)"', html, re.I))
            event_links = sorted(l for l in links if "event" in l.lower() or "show" in l.lower() or "calendar" in l.lower())
            print(f"[{name}] {status} OK => {final_url}")
            for l in event_links[:8]:  # Cap output at 8 candidate links per venue.
                print(f"    {l}")
    except urllib.error.HTTPError as e:
        print(f"[{name}] HTTP {e.code} => {url}")
    except Exception as e:
        print(f"[{name}] ERROR: {e}")
