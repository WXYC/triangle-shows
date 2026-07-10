#!/usr/bin/env python3
"""
Dev utility that fires POST /api/scrape against a running Triangle Shows instance
and prints a structured summary of the scrape results to stdout and a log file.

Role: Developer tool — not part of the production runtime. Used to manually trigger
a scrape and inspect per-venue results without digging through server logs. In
production, scrapes are triggered automatically by Cloud Scheduler → POST /api/scrape.
Requires: A running Triangle Shows server (local or production). No .env or DB access
needed — all communication goes through the HTTP API.
"""

# --- Imports ---
import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# --- Constants ---
BASE_URL = "https://triangle-shows.net"
# Log file lives next to this script so it's easy to find after a run
LOG_FILE = Path(__file__).parent / "scrape_results.log"


def main():
    """Parse CLI args, POST /api/scrape, and print + log a structured summary."""
    # --- Argument parsing ---
    parser = argparse.ArgumentParser(description="Trigger scrape and log results")
    parser.add_argument("--type", dest="scraper_type", help="Scraper type to run (e.g. rhp_events)")
    parser.add_argument("--url", default=BASE_URL, help=f"Base URL (default: {BASE_URL})")
    args = parser.parse_args()

    # --- Build request URL ---
    endpoint = f"{args.url.rstrip('/')}/api/scrape"
    if args.scraper_type:
        # Optional filter — tells the server to run only one scraper instead of all
        endpoint += f"?scraper_type={args.scraper_type}"

    started = datetime.now()
    # Label used in console messages to show which scraper(s) are running
    label = f"[{args.scraper_type or 'ALL'}]"

    print(f"{label} POST {endpoint}")
    print(f"{label} Started at {started.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{label} Writing results to {LOG_FILE}")

    # --- Fire the scrape request ---
    try:
        # Content-Length: 0 is required for POST requests with no body
        req = urllib.request.Request(endpoint, method="POST", headers={"Content-Length": "0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            status_code = resp.status
    except urllib.error.HTTPError as e:
        # Server returned an error status — still read the body for error details
        raw = e.read()
        status_code = e.code
    except urllib.error.URLError as e:
        # Network-level failure (server not running, DNS error, etc.)
        print(f"ERROR: Could not reach {endpoint}: {e.reason}", file=sys.stderr)
        sys.exit(1)

    elapsed = (datetime.now() - started).total_seconds()

    # --- Parse JSON response ---
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Unexpected non-JSON response (e.g. nginx error page)
        data = None

    # --- Build output lines ---
    lines = []
    lines.append("=" * 70)
    lines.append(f"Scrape run — {started.strftime('%Y-%m-%d %H:%M:%S')}  ({elapsed:.1f}s)")
    lines.append(f"Endpoint : {endpoint}")
    lines.append(f"HTTP     : {status_code}")
    lines.append("=" * 70)

    if data is None:
        lines.append(f"Non-JSON response ({status_code}):")
        lines.append(raw.decode(errors="replace"))
    elif "detail" in data:
        # FastAPI error (500 with our new handler)
        lines.append(f"ERROR: {data['detail']}")
    elif "results" in data:
        # Normal success path — data["results"] is a list of per-venue dicts
        results = data["results"]
        successes = [r for r in results if r.get("status") == "success"]
        failures  = [r for r in results if r.get("status") != "success"]

        lines.append(f"Venues  : {len(results)}  ({len(successes)} ok, {len(failures)} failed)")
        # Aggregate event counts across all successful scrapers
        total_found      = sum(r.get("found",      0) for r in successes)
        total_created    = sum(r.get("created",    0) for r in successes)
        total_updated    = sum(r.get("updated",    0) for r in successes)
        total_tombstoned = sum(r.get("tombstoned", 0) for r in successes)
        total_relisted   = sum(r.get("relisted",   0) for r in successes)
        lines.append(
            f"Events  : {total_found} found  |  {total_created} created  |  {total_updated} updated"
            f"  |  {total_tombstoned} tombstoned  |  {total_relisted} relisted"
        )
        lines.append("")

        # Successes
        if successes:
            lines.append("  OK venues:")
            for r in successes:
                lines.append(
                    f"    OK  {r['venue']:<30}  "
                    f"found={r.get('found',0):>3}  "
                    f"created={r.get('created',0):>3}  "
                    f"updated={r.get('updated',0):>3}  "
                    f"tombstoned={r.get('tombstoned',0):>3}  "
                    f"relisted={r.get('relisted',0):>3}"
                )

        # Failures
        if failures:
            lines.append("")
            lines.append("  FAILED venues:")
            for r in failures:
                lines.append(f"    ERR {r['venue']:<30}  {r.get('error', 'unknown error')}")
    else:
        # Unexpected response shape — dump raw JSON for inspection
        lines.append(json.dumps(data, indent=2))

    lines.append("")

    # --- Print and log output ---
    output = "\n".join(lines)
    print(output)

    # Append (not overwrite) so previous runs are preserved for comparison
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(output + "\n")

    print(f"{label} Done in {elapsed:.1f}s — appended to {LOG_FILE.name}")

    # Exit with a non-zero code if any venue scrapers failed, so CI/shell scripts can detect it
    if data and "results" in data:
        failures = [r for r in data["results"] if r.get("status") != "success"]
        if failures:
            sys.exit(1)


if __name__ == "__main__":
    main()
