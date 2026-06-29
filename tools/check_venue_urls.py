"""
Spot-checks HTTP status codes for venue event page URLs defined inline.

Role: Developer utility — run manually to diagnose scraper failures caused by
unreachable or redirecting venue URLs. Not part of the runtime scrape pipeline.
Requires: No env vars or DB connection; hits external URLs directly over HTTP.
"""

# --- Imports ---
import urllib.request
import urllib.error

# --- Venue URLs to check ---
# Each entry is (scraper-slug, url). Extend this list when adding new venues
# or when a scraper starts returning empty results and you want to verify reachability.
# moon-room and neptunes entries test both JSON and plain variants so we can
# see which endpoint the site accepts without 4xx/5xx errors.
venues = [
    ("motorco",         "https://www.motorcomusic.com/events/"),
    ("kings",           "https://www.kingsbarcade.com/events/"),
    ("the-cave",        "https://www.caverntavern.com/events/"),
    ("moon-room",       "https://www.moonroomraleigh.com/events?format=json"),
    ("moon-room-alt",   "https://www.moonroomraleigh.com/events"),
    ("neptunes-parlour","https://neptunesparlour.com/events?format=json"),
    ("neptunes-no-www", "https://neptunesparlour.com/events"),
]

# --- Request headers ---
# Mimic a real browser User-Agent; many venue sites return 403 for bare Python
# urllib requests without one.
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# --- URL check loop ---
for name, url in venues:
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.url  # may differ from url if the server redirected
            status = resp.status
            print(f"[{name}] {status} OK  =>  {final_url}")
    except urllib.error.HTTPError as e:
        print(f"[{name}] HTTP {e.code}  =>  {url}")
    except urllib.error.URLError as e:
        print(f"[{name}] ERROR: {e.reason}  =>  {url}")
    except Exception as e:
        print(f"[{name}] ERROR: {e}  =>  {url}")
