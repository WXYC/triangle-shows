"""
Inspects JS-heavy venue pages to understand their event data structure.

Role: Developer utility — run manually when building or debugging scrapers for
venues that use JavaScript to render events (FullCalendar inline data, event
card HTML, or admin-ajax endpoints). Not part of the runtime scrape pipeline.
Requires: No env vars. Fetches live pages via urllib; run from any Python env.
"""

# --- Imports ---
import urllib.request
import re
import json

# --- Constants ---

# Browser-like headers to avoid being blocked by venue sites
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# --- Helpers ---

def fetch(url):
    """Fetch a URL and return the response body as a UTF-8 string."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


# ─── Motorco: extract inline FullCalendar events using regex per-field ────────
# Motorco embeds FullCalendar event objects directly in the page HTML as a JS
# array with single-quoted strings and unquoted keys — not valid JSON, so we
# use per-field regex rather than json.loads.
print("=" * 60)
print("MOTORCO: Extracting inline FullCalendar events")
print("=" * 60)
html = fetch("https://motorcomusic.com/calendar/")

# Extract all JS event objects using per-field regex (JS uses single quotes, unquoted keys)
event_blocks = re.findall(
    r'\{[^{}]*?title\s*:\s*[\'"](.+?)[\'"][^{}]*?start\s*:\s*[\'"](.+?)[\'"]'
    r'[^{}]*?(?:end\s*:\s*[\'"](.+?)[\'"])?[^{}]*?url\s*:\s*[\'"](.+?)[\'"][^{}]*?\}',
    html, re.S
)
print(f"Found {len(event_blocks)} events via regex")
print("\nFirst 5 events (title, start, end, url):")
for i, (title, start, end, url) in enumerate(event_blocks[:5]):
    print(f"  [{i+1}] title={title!r}")
    print(f"       start={start!r}")
    print(f"       end={end!r}")
    print(f"       url={url!r}")

# ─── Carolina Theatre: inspect event card HTML ────────────────────────────────
# Carolina Theatre renders events as repeated "eventCard" div blocks in static
# HTML. This section reveals the card structure so the scraper knows which
# selectors to use for title, date, and URL.
print("\n" + "=" * 60)
print("CAROLINA THEATRE: Inspecting eventCard HTML structure")
print("=" * 60)
html = fetch("https://carolinatheatre.org/events/")

# Find first card with class "eventCard"
m = re.search(r'class="[^"]*eventCard[^"]*"', html)
if m:
    # Back up to find the opening tag and show 1500 chars
    start = max(0, m.start() - 50)
    chunk = html[start:start + 1500]
    print("First eventCard block (raw HTML):")
    print(chunk)
else:
    print("No eventCard class found")

# Also show event__dateBox structure
print("\n--- event__dateBox context ---")
m2 = re.search(r'.{200}event__dateBox.{300}', html, re.S)
if m2:
    print(m2.group(0))

# ─── Kings: find event-related URLs and action names ─────────────────────────
# Kings uses the EventPrime WordPress plugin, which loads events via admin-ajax
# POST requests authenticated by a nonce. This section sniffs for the nonce
# value and the specific `ep_*` action name needed to replicate those calls.
print("\n" + "=" * 60)
print("KINGS: Scraping event URLs and admin-ajax action names")
print("=" * 60)
page_html = fetch("https://www.kingsraleigh.com/")

# Find nonce (various formats)
nonce_patterns = [
    r'"(?:security_nonce|nonce|ep_nonce|event_wishlist_nonce)"\s*:\s*"([a-f0-9]+)"',
    r'nonce["\s:=]+["\']([a-f0-9]{10})["\']',
]
for pat in nonce_patterns:
    m = re.search(pat, page_html)
    if m:
        print(f"Nonce found: {m.group(1)!r}")
        break
else:
    print("No nonce found")

# Find action names in EventPrime JS
actions = re.findall(r'"action"\s*:\s*"(ep[_\w]+)"', page_html)
actions += re.findall(r"'action'\s*:\s*'(ep[_\w]+)'", page_html)
actions += re.findall(r'action\s*=\s*["\'](\w+events?\w*)["\']', page_html)
print(f"Found admin-ajax actions: {list(set(actions))}")

# Find event-related URLs
ep_urls = list(set(re.findall(r'https?://[^\s"\']+event[^\s"\']*', page_html)))
print(f"\nEvent-related URLs on Kings page ({len(ep_urls)} found):")
for u in ep_urls[:20]:
    print(" ", u)

# Show portion of eventprime JS config — useful for discovering the ajax_url
# and other parameters EventPrime injects as a global JS object
m3 = re.search(r'eventprime\s*=\s*(\{.{0,3000})', page_html, re.S)
if m3:
    print("\nEventprime config (first 1500 chars):")
    print(m3.group(1)[:1500])
