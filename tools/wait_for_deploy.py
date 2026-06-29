#!/usr/bin/env python3
"""
Polls /api/health until the deployed git SHA matches the local HEAD commit.

Role: Developer utility — run manually after pushing to confirm a Cloud Run deploy
has gone live. Not part of the runtime scrape or serving path.
Requires: git available on PATH; network access to the deployed app's /api/health
endpoint (which returns a JSON body with a "version" field set to the deployed SHA).

Usage:
    python tools/wait_for_deploy.py
    python tools/wait_for_deploy.py --url http://localhost:8000
    python tools/wait_for_deploy.py --interval 15
"""

# --- Imports ---

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

# --- Constants ---

BASE_URL = "https://triangle-shows.net"
DEFAULT_INTERVAL = 20  # seconds between health-check polls


# --- Helpers ---

def get_local_sha():
    """Return the full SHA of the local HEAD commit."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"ERROR: Could not get local git SHA: {e}", file=sys.stderr)
        sys.exit(1)


def get_deployed_version(url):
    """Fetch the 'version' field from /api/health, returning an error string on failure."""
    try:
        req = urllib.request.Request(f"{url}/api/health")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            # The health endpoint embeds the deployed git SHA as "version"
            return data.get("version", "unknown")
    except urllib.error.URLError as e:
        # App is still coming up or unreachable — not a fatal error, just keep polling
        return f"(unreachable: {e.reason})"
    except Exception as e:
        return f"(error: {e})"


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Poll until deployed version matches local HEAD")
    parser.add_argument("--url", default=BASE_URL, help=f"Base URL (default: {BASE_URL})")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL})")
    args = parser.parse_args()

    local_sha = get_local_sha()
    short = local_sha[:7]  # abbreviated SHA for display

    print(f"Waiting for deploy of {short} to {args.url}")
    print(f"Polling every {args.interval}s — Ctrl+C to cancel\n")

    start = datetime.now()
    attempt = 0

    while True:
        attempt += 1
        deployed = get_deployed_version(args.url)
        elapsed = (datetime.now() - start).total_seconds()
        ts = datetime.now().strftime("%H:%M:%S")

        # Compare with startswith in both directions to handle full vs. short SHA mismatches
        if deployed.startswith(local_sha) or local_sha.startswith(deployed):
            print(f"[{ts}] DEPLOYED  version={deployed[:7]}  ({elapsed:.0f}s)")
            break
        else:
            print(f"[{ts}] waiting   deployed={deployed[:7]}  want={short}  ({elapsed:.0f}s)")

        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(0)


if __name__ == "__main__":
    main()
