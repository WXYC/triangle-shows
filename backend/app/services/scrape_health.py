"""
Pure scrape-health evaluator: turns a venue's recent ScrapeLog rows into a verdict.

Role: Consumed directly by GET /api/v1/health/scrapers (app/api/v1.py); the future
alerting PR (issue #86 part 2) reuses it unchanged for the daily digest's
transition detection, replaying the same rows at ``now - 24h``. Strictly pure by
contract: no settings reads and no ambient clock anywhere in this module -- ``now``
is always an explicit parameter, never ``datetime.utcnow()`` read internally, and
``evaluate_staleness`` is always an explicit parameter, never a read of
``settings.ENABLE_SCHEDULER``. The test suite was bitten once by an ambient-clock
test (commit 6357814); this module's whole shape is designed so that can't happen
here. Read-only over its inputs by construction -- it never mutates or persists a
ScrapeLog row; the org's data-safety rule treats scrape history as the baseline.

Duck-typed on purpose (see _Venue/_ScrapeLog below) rather than importing
app.models: nothing here needs a live database, a session, or even app.database's
module-level engine construction, so a caller can unit-test this module with plain
dataclasses and nothing else.

Requires: app.cadence (group_for / max_gap_hours) for the staleness threshold.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Protocol, Sequence

from app.cadence import group_for, max_gap_hours

# --- Types the evaluator's inputs must satisfy (structural, not nominal) ---


class _Venue(Protocol):
    slug: str
    scraper_type: str


class _ScrapeLogEntry(Protocol):
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    events_found: int
    error_message: Optional[str]


# --- Thresholds (engine-level constants; no per-venue overrides -- issue #86) ---

# How many of the most recent attempts must agree before the consecutive-failure or
# silent-zero signal fires. A single bad run is noise (a venue site hiccup, one
# genuinely dark week); a streak of this length is the signal.
CONSECUTIVE_WINDOW = 3

# Trailing window used to decide whether a venue "normally" returns events, so a
# legitimately quiet venue (a gallery with one show a month) doesn't get flagged for
# a silent-zero streak that is actually normal for it.
BASELINE_WINDOW_DAYS = 30

# Staleness fires at this multiple of the venue's expected max inter-run gap
# (app.cadence.max_gap_hours) -- 2x, not 1x, so a single missed run never pages.
STALENESS_MULTIPLIER = 2

# --- Verdict ---


@dataclass(frozen=True)
class ScrapeHealthVerdict:
    """One venue's health verdict.

    Field names mirror ScraperHealthResponse (app/schemas.py) 1:1 so the API layer
    can build one from the other with no per-field mapping.
    """

    venue_slug: str
    status: str  # "ok" | "warning" | "critical" | "unknown"
    signal: Optional[str]  # None on ok/unknown
    detail: Optional[str]  # already scrubbed -- see scrub_error_message below
    last_success_at: Optional[datetime]
    last_attempt_at: Optional[datetime]


# --- Redaction ---

# Matches a literal "?" through to the next whitespace character or end-of-string.
# Deliberately not anchored on a closing quote/bracket/paren: ScrapeLog.error_message
# is stored as str(e)[:2000], so a long error can be truncated mid-query-string --
# possibly mid "apikey=" -- leaving no terminator for a pattern to anchor on. \S*
# (zero or more non-whitespace) already degrades gracefully to end-of-string in that
# case. This is a blunter, whole-query-string strip than app.redaction.redact_credentials
# (a denylist of parameter *names*) on purpose: an unauthenticated endpoint and a
# third-party chat webhook are both denylist-blind sinks, so nothing short of "assume
# any query string could be live" is safe here (issue #86 correction, 2026-08-16).
_QUERY_STRING_RE = re.compile(r"\?\S*")


def scrub_error_message(message: Optional[str]) -> Optional[str]:
    """Strip any embedded query string from `message`, "?" through whitespace-or-EOS.

    The single sanitization point GET /api/v1/health/scrapers and the future digest
    job (issue #86 part 2) both read `detail` from -- so ScrapeLog.error_message is
    scrubbed exactly once, not once per consumer. venue_slug still identifies the
    venue, so no triage value is lost by removing the query string outright.
    """
    if not message:
        return message
    return _QUERY_STRING_RE.sub("", message)


# --- Evaluator ---


def evaluate_venue_health(
    venue: _Venue,
    logs: Sequence[_ScrapeLogEntry],
    *,
    now: datetime,
    evaluate_staleness: bool,
) -> ScrapeHealthVerdict:
    """Evaluate one venue's health from its recent ScrapeLog rows.

    `now` is naive UTC, matching the ScrapeLog timestamp columns, and is always
    supplied by the caller -- never read from the clock here. Rows with
    `started_at > now` are excluded by contract, not merely as a courtesy: this is
    what makes a replay at an earlier `now` (the future digest job's transition
    detection) a true replay rather than one still seeing rows that "hadn't
    happened yet" at that earlier instant.

    `logs` need not be every row the venue has, but the silent-zero baseline guard can
    only see what it is given: if the venue has a nonzero-events success inside
    BASELINE_WINDOW_DAYS and that row is missing from `logs`, the guard concludes the
    venue never shows events and downgrades a genuine warning to ok. That failure is
    silent and inverted, so a caller that caps its fetch owes this function the baseline
    row explicitly rather than assuming a row count reaches far enough back (see
    api/v1.py, which fetches it as its own query).

    `evaluate_staleness` gates signal 3 only. The endpoint passes
    `settings.ENABLE_SCHEDULER` (staleness is meaningless when nothing is scheduled
    to run); the future digest job passes True unconditionally, since
    configure_scheduler() only ever runs under `if settings.ENABLE_SCHEDULER` in
    main.py, so the digest existing at all already implies the scheduler is on.
    """
    visible = [log for log in logs if log.started_at <= now]

    if not visible:
        return ScrapeHealthVerdict(
            venue_slug=venue.slug,
            status="unknown",
            signal=None,
            detail="no scrape has ever run for this venue",
            last_success_at=None,
            last_attempt_at=None,
        )

    ordered = sorted(visible, key=lambda log: log.started_at, reverse=True)
    last_attempt_at = ordered[0].started_at
    # finished_at is nullable, so a success row can carry no completion timestamp (a
    # hand-backfilled row, or a future path that commits before stamping it). Skip
    # those rather than reporting the newest one's None: yielding None here would say
    # "this venue has never succeeded" while older successes that *do* carry a
    # timestamp sit right there in the same history.
    last_success_at = next(
        (
            log.finished_at
            for log in ordered
            if log.status == "success" and log.finished_at is not None
        ),
        None,
    )
    recent = ordered[:CONSECUTIVE_WINDOW]
    has_full_window = len(recent) == CONSECUTIVE_WINDOW

    # Signals are evaluated in descending severity, and staleness comes first even
    # though it is numbered last in issue #86. Order is load-bearing, not cosmetic:
    # these conditions overlap, and the first match wins.
    #
    # A venue that stops being scraped altogether keeps whatever its last few attempts
    # looked like. If those were zero-event successes, checking silent-zero first
    # returns warning ("possible bot wall or markup change") for a venue that has in
    # fact vanished from the schedule -- an operator triaging by severity deprioritizes
    # exactly the venue that most needs attention, and part 2's digest inherits the
    # mis-ranking. Staleness also outranks consecutive failures: when both hold, "not
    # being scraped at all" is the root cause and the stored error is a stale artifact
    # of the last attempt, so leading with it would point triage at the venue's site
    # when the scheduler is what broke.

    # Signal 3 (highest severity): staleness -- no attempt within 2x the expected max gap.
    if evaluate_staleness:
        threshold = timedelta(hours=STALENESS_MULTIPLIER * max_gap_hours(group_for(venue)))
        if now - last_attempt_at > threshold:
            return ScrapeHealthVerdict(
                venue_slug=venue.slug,
                status="critical",
                signal="stale",
                detail="venue not being scraped at all",
                last_success_at=last_success_at,
                last_attempt_at=last_attempt_at,
            )

    # Signal 1: consecutive hard failures.
    if has_full_window and all(log.status == "failed" for log in recent):
        return ScrapeHealthVerdict(
            venue_slug=venue.slug,
            status="critical",
            signal="consecutive_failures",
            detail=scrub_error_message(recent[0].error_message),
            last_success_at=last_success_at,
            last_attempt_at=last_attempt_at,
        )

    # Signal 2 (lowest severity): silent zero, guarded by a 30-day baseline of nonzero
    # successes. The caller must supply that baseline row in `logs` if one exists --
    # see the note on truncation in the docstring above.
    if has_full_window and all(
        log.status == "success" and log.events_found == 0 for log in recent
    ):
        baseline_cutoff = now - timedelta(days=BASELINE_WINDOW_DAYS)
        has_baseline = any(
            log.status == "success"
            and log.events_found > 0
            and log.started_at >= baseline_cutoff
            for log in visible
        )
        if has_baseline:
            return ScrapeHealthVerdict(
                venue_slug=venue.slug,
                status="warning",
                signal="silent_zero",
                detail="possible bot wall or markup change",
                last_success_at=last_success_at,
                last_attempt_at=last_attempt_at,
            )

    return ScrapeHealthVerdict(
        venue_slug=venue.slug,
        status="ok",
        signal=None,
        detail=None,
        last_success_at=last_success_at,
        last_attempt_at=last_attempt_at,
    )
