"""
Fetches raw HTML from venue calendar pages and highlights event-related patterns.

Role: Developer utility — run manually to reverse-engineer a venue's HTML structure
before writing or debugging a scraper. Not part of the runtime scrape pipeline.
Requires: Internet access and the target venue URLs to be reachable. No env vars needed.
"""

# --- Imports ---
import urllib.request
import re

# --- Constants ---

# Mimic a real browser so venue sites don't block the request
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
    """Download and return the full HTML of a URL as a UTF-8 string."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", errors="ignore")

def show_around(html, pattern, chars=800, label=""):
    """Search for a regex pattern and print the surrounding HTML context."""
    m = re.search(pattern, html, re.I)
    if m:
        # Back up 100 chars before the match so we see the opening tag
        start = max(0, m.start() - 100)
        print(f"\n  [{label or pattern}] found at pos {m.start()}:")
        print(html[start:start+chars])
    else:
        print(f"\n  [{label or pattern}] NOT FOUND")

# --- Main: per-venue inspection ---

print("\n" + "="*60)
print("MOTORCO — looking for tc-responsive-event")
print("="*60)
html = fetch("https://motorcomusic.com/calendar/")
show_around(html, r'tc-responsive-event', chars=600, label="tc-responsive-event")

print("\n" + "="*60)
print("CATS CRADLE — looking for rhp-event structure")
print("="*60)
html = fetch("https://catscradle.com/events/")
show_around(html, r'rhp-event__date', chars=800, label="rhp-event BEM")

print("\n" + "="*60)
print("KINGS — looking for any event/calendar structure")
print("="*60)
html = fetch("https://www.kingsraleigh.com/")
# Show the middle section of the page where events likely are
mid = len(html) // 3
print(html[mid:mid+1500])
