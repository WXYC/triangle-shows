"""Show full first event block from Cat's Cradle."""
import urllib.request
import re

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"}

url = "https://catscradle.com/events/"
req = urllib.request.Request(url, headers=HEADERS)
with urllib.request.urlopen(req, timeout=15) as r:
    html = r.read().decode("utf-8", errors="ignore")

# Find first eventWrapper and show 3000 chars from there
m = re.search(r'class\s*=\s*"[^"]*eventWrapper[^"]*"', html)
if m:
    chunk = html[m.start():m.start() + 3000]
    print(chunk)
else:
    print("eventWrapper not found")
