"""Inspect the RHP event wrapper structure in detail."""
import urllib.request
import re

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

url = "https://catscradle.com/events/"
req = urllib.request.Request(url, headers=HEADERS)
with urllib.request.urlopen(req, timeout=15) as r:
    html = r.read().decode("utf-8", errors="ignore")

# Find the first event block — back up 1500 chars from the date element to find wrapper
m = re.search(r'rhp-event__date--list', html)
if m:
    start = max(0, m.start() - 1500)
    print(html[start:m.start() + 500])
