#!/usr/bin/env python3
"""
Prints a month's unit-economics rollup as paste-ready markdown for the ledgers in
``docs/economics/``.

Role: Ops script for the unit-economics experiment (#87) — what the scrapers cost,
what anyone consumed, and what is on the shelf, for one calendar month. Read-only:
every query runs through ``app.services.economics``, which never writes.

Requires a database. Unlike its neighbours in this directory, this script imports the
backend package rather than talking to the HTTP API — the numbers it needs are
aggregates over three tables and no endpoint exposes them. That means it needs the
backend's virtualenv, and it needs to be told where the database is:

    backend/.venv/bin/python tools/unit_economics_report.py --month 2026-07

    DATABASE_URL=postgresql+asyncpg://user:pass@host/db \\
        backend/.venv/bin/python tools/unit_economics_report.py --month 2026-07

The second form is what you want for production numbers. ``backend/.env`` is loaded
relative to the working directory, so running from the repo root it does not load at
all, and ``app.config``'s default points at local dev — a run with no ``DATABASE_URL``
reports on your laptop, quietly and plausibly.

It stays in ``tools/`` regardless: this directory and its README are where an operator
looks for ops scripts, and the virtualenv requirement is a documented caveat rather
than a reason to hide the script under ``backend/``.
"""

# --- Imports ---
import argparse
import asyncio
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# Put backend/ on sys.path so `import app...` resolves when running from the repo root.
# Same bootstrap as backend/alembic/env.py, which loads the app package the same way.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.economics import (  # noqa: E402
    MONTH_FORMAT,
    TRAILING_CLIENT_WINDOW_DAYS,
    ZERO_EVENT_STREAK_DAYS,
    MonthReport,
    collect_month_report,
    last_full_month,
    parse_month,
)

logger = logging.getLogger("unit_economics_report")


# --- Formatting helpers ---

def _pct(value: float | None) -> str:
    """A success rate as a percentage, or an em dash when there is no rate to report."""
    return "—" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None, places: int = 1) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _int(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """A markdown table as a list of lines.

    Header and separator are generated from the same list, so they cannot disagree on
    column count — a ragged table renders as literal text wherever it is pasted.
    """
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _feed_status_lines(report: MonthReport) -> list[str]:
    """The instrumentation-status paragraph that keeps a zero from lying.

    ``record_feed_fetch`` no-ops when ``TELEMETRY_SALT`` is empty, so a month with no
    rows means one of two very different things. #87 pre-registered a threshold against
    the distinct-client count, and evaluating it against "nothing was ever recorded"
    would answer a question nobody asked.
    """
    feed = report.feed
    if not feed.salt_configured and not feed.has_rows:
        return [
            "**Telemetry not enabled for this window.** `TELEMETRY_SALT` is unset, so "
            "`record_feed_fetch` wrote nothing — this section reports the absence of "
            "instrumentation, not the absence of demand. Do not read it as a measured "
            "result, and do not evaluate the experiment's threshold against it.",
        ]

    lines = []
    if not feed.has_rows:
        lines.append(
            f"**No rows in window.** `TELEMETRY_SALT` is configured, so this is a measured "
            f"zero: the feed was instrumented across all {feed.days_in_window} days of the "
            f"month and served no fetches."
        )
    else:
        coverage = f"{feed.days_with_rows} of {feed.days_in_window} days carried at least one fetch"
        first = feed.first_fetch_at.isoformat(sep=" ") if feed.first_fetch_at else "—"
        last = feed.last_fetch_at.isoformat(sep=" ") if feed.last_fetch_at else "—"
        lines.append(f"Instrumented; {coverage}. First row {first} UTC, last row {last} UTC.")
        if feed.days_with_rows < feed.days_in_window:
            lines.append(
                "Coverage is partial — totals below describe the days that carried rows, "
                "not a full month."
            )
    if not feed.salt_configured:
        lines.append(
            "`TELEMETRY_SALT` is **currently unset** even though the window carries rows, "
            "so collection has stopped since. Later windows will be empty for that reason."
        )
    return lines


def render_markdown(report: MonthReport) -> str:
    """The whole month as markdown, ready to paste into a `docs/economics/` ledger."""
    month_label = f"{report.month:%Y-%m}"
    lines: list[str] = [
        f"# Unit economics — {month_label}",
        "",
        f"Window: `{report.window_start.isoformat(sep=' ')}` to "
        f"`{report.window_end.isoformat(sep=' ')}` UTC "
        f"({month_label} calendar month in {report.timezone_name}).",
        "",
        "## Scrape cost by venue",
        "",
    ]

    venue_rows = [
        [
            f"`{v.venue_slug}`",
            v.venue_name,
            _int(v.attempts),
            _int(v.failures),
            _pct(v.success_rate),
            _num(v.mean_duration_seconds),
            _num(v.total_duration_seconds),
            _int(v.last_events_found),
            "yes" if v.zero_event_streak else "",
        ]
        for v in report.venues
    ]
    lines += _table(
        [
            "Venue",
            "Name",
            "Attempts",
            "Failures",
            "Success",
            "Mean s",
            "Total s",
            "Last found",
            f"Empty {ZERO_EVENT_STREAK_DAYS}d",
        ],
        venue_rows,
    )

    total_attempts = sum(v.attempts for v in report.venues)
    total_failures = sum(v.failures for v in report.venues)
    total_seconds = sum(v.total_duration_seconds for v in report.venues)
    flagged = [v.venue_slug for v in report.venues if v.zero_event_streak]
    lines += [
        "",
        f"Totals: {total_attempts:,} attempts, {total_failures:,} failures, "
        f"{total_seconds / 60:.1f} minutes of scraping across {len(report.venues)} venues.",
        "",
    ]
    if flagged:
        lines += [
            f"Scraping successfully but finding nothing for the last {ZERO_EVENT_STREAK_DAYS} "
            f"days of the month: {', '.join(f'`{slug}`' for slug in flagged)}. That is the "
            "signature of a venue whose page changed shape without breaking — worth a look "
            "before trusting this month's inventory numbers.",
            "",
        ]

    lines += ["## Feed demand", ""]
    lines += _feed_status_lines(report)
    lines += [""]

    feed = report.feed
    lines += _table(
        ["Measure", "Value"],
        [
            ["Fetches served", _int(feed.total_fetches)],
            ["Distinct clients (in month)", _int(feed.distinct_clients)],
            [
                f"Distinct clients (trailing {TRAILING_CLIENT_WINDOW_DAYS}d at month end)",
                _int(feed.trailing_28d_distinct_clients),
            ],
            ["Full-calendar fetches", _int(feed.full_feed_fetches)],
            ["Venue-filtered fetches", _int(feed.filtered_fetches)],
        ],
    )

    if feed.per_venue_fetches:
        lines += [
            "",
            "Filtered subscriptions by venue. A subscription naming several venues counts "
            "once toward each, so this column sums to more than the filtered-fetch total.",
            "",
        ]
        lines += _table(
            ["Venue", "Filtered fetches"],
            [[f"`{slug}`", _int(n)] for slug, n in feed.per_venue_fetches.items()],
        )

    inventory = report.inventory
    lines += [
        "",
        "## Inventory",
        "",
    ]
    lines += _table(
        ["Measure", "Value"],
        [
            ["Live upcoming events (as of now)", _int(inventory.live_upcoming_events)],
            ["Events created in month (live)", _int(inventory.events_created_in_month)],
            [
                "Events created in month, since tombstoned",
                _int(inventory.events_created_in_month_tombstoned),
            ],
        ],
    )
    lines += [""]
    return "\n".join(lines)


# --- Entry point ---

async def _build_report(month: date) -> MonthReport:
    """Open one session, collect, close. Imported lazily so --help needs no database."""
    from app.database import async_session

    async with async_session() as session:
        return await collect_month_report(session, month)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a month's unit-economics rollup as markdown.",
        epilog=(
            "Set DATABASE_URL to report against a database other than the local-dev "
            "default; backend/.env is not loaded when running from the repo root."
        ),
    )
    parser.add_argument(
        "--month",
        help=f"Month to report, as {MONTH_FORMAT}. Defaults to the last full market-time month.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Log the window and query progress to stderr."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        month = parse_month(args.month) if args.month else last_full_month()
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # unreachable; parser.error exits 2. Kept so the signature is honest.

    if month >= datetime.now().date().replace(day=1) and args.month is None:
        logger.warning("Reporting on %s, which is not a completed month.", f"{month:%Y-%m}")

    logger.info("Collecting unit-economics rollup for %s", f"{month:%Y-%m}")
    try:
        report = asyncio.run(_build_report(month))
    except Exception:
        logger.exception(
            "Failed to collect the rollup. Check DATABASE_URL — with none set, this "
            "connects to the local-dev default rather than production."
        )
        return 1

    print(render_markdown(report))
    logger.info(
        "Done: %d venues, %d feed fetches, %d live upcoming events",
        len(report.venues),
        report.feed.total_fetches,
        report.inventory.live_upcoming_events,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
