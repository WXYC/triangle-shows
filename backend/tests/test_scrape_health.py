"""Tests for app.services.scrape_health -- the pure scrape-health evaluator
(issue #86, part 1: detection and exposure).

Every test drives evaluate_venue_health with a fixed `now` instant passed
explicitly; none of them touch the wall clock or app.config.settings. That is a
deliberate pin, not an incidental style choice -- the suite was bitten once by an
ambient-clock test (commit 6357814), and this evaluator's whole design exists to
make that class of bug impossible here.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pytest

from app.services.scrape_health import (
    BASELINE_WINDOW_DAYS,
    CONSECUTIVE_WINDOW,
    ScrapeHealthVerdict,
    evaluate_venue_health,
    scrub_error_message,
)

NOW = datetime(2026, 8, 16, 12, 0, 0)


@dataclass
class _Venue:
    slug: str = "cats-cradle"
    scraper_type: str = "venuepilot"  # classifies as "indie" via app.cadence.group_for


@dataclass
class _Log:
    started_at: datetime
    status: str
    events_found: int = 0
    finished_at: Optional[datetime] = field(default=None)
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.finished_at is None and self.status != "running":
            self.finished_at = self.started_at + timedelta(seconds=5)


def _attempts_every(hours: int, count: int, *, status: str = "success", **kwargs) -> list[_Log]:
    """`count` attempts spaced `hours` apart, most recent at NOW."""
    return [
        _Log(started_at=NOW - timedelta(hours=hours * i), status=status, **kwargs)
        for i in range(count)
    ]


# --- unknown: never scraped ---


def test_zero_logs_report_unknown():
    verdict = evaluate_venue_health(_Venue(), [], now=NOW, evaluate_staleness=True)
    assert verdict.status == "unknown"
    assert verdict.signal is None
    assert verdict.last_attempt_at is None
    assert verdict.last_success_at is None


def test_logs_entirely_in_the_future_report_unknown():
    """started_at > now rows are excluded by contract -- a venue whose only rows are
    "in the future" relative to `now` must read exactly like a never-scraped venue."""
    future_logs = [_Log(started_at=NOW + timedelta(hours=1), status="success", events_found=5)]
    verdict = evaluate_venue_health(_Venue(), future_logs, now=NOW, evaluate_staleness=True)
    assert verdict.status == "unknown"


def test_a_future_row_does_not_count_toward_a_failure_streak():
    """Two real failures plus one future-dated failure must NOT complete the
    3-in-a-row streak -- only rows visible as of `now` count."""
    logs = [
        _Log(started_at=NOW + timedelta(hours=1), status="failed"),  # excluded
        _Log(started_at=NOW - timedelta(hours=1), status="failed"),
        _Log(started_at=NOW - timedelta(hours=2), status="failed"),
    ]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.status == "ok"


# --- signal 1: consecutive hard failures ---


def test_three_consecutive_failures_report_critical():
    logs = _attempts_every(6, CONSECUTIVE_WINDOW, status="failed", error_message="boom")
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.status == "critical"
    assert verdict.signal == "consecutive_failures"
    assert verdict.detail == "boom"


def test_consecutive_failure_detail_carries_the_latest_error_scrubbed():
    logs = [
        _Log(started_at=NOW, status="failed", error_message="latest failure?apikey=SECRET"),
        _Log(started_at=NOW - timedelta(hours=6), status="failed", error_message="older?apikey=SECRET"),
        _Log(started_at=NOW - timedelta(hours=12), status="failed", error_message="oldest?apikey=SECRET"),
    ]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.detail == "latest failure"


@pytest.mark.parametrize("failure_count", [1, 2])
def test_fewer_than_the_full_window_of_failures_does_not_trigger_critical(failure_count):
    """One-off failures are noise -- only a full streak is the signal."""
    logs = _attempts_every(6, failure_count, status="failed", error_message="boom")
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.status != "critical"


def test_a_success_breaking_the_streak_prevents_critical():
    logs = [
        _Log(started_at=NOW, status="success", events_found=3),
        _Log(started_at=NOW - timedelta(hours=6), status="failed"),
        _Log(started_at=NOW - timedelta(hours=12), status="failed"),
    ]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.status != "critical"


# --- signal 2: silent zero, with the 30-day baseline guard ---


def test_silent_zero_reports_warning_when_the_baseline_shows_normal_events():
    recent_zero = _attempts_every(6, CONSECUTIVE_WINDOW, status="success", events_found=0)
    baseline_success = _Log(
        started_at=NOW - timedelta(days=10), status="success", events_found=8
    )
    verdict = evaluate_venue_health(
        _Venue(), recent_zero + [baseline_success], now=NOW, evaluate_staleness=False
    )
    assert verdict.status == "warning"
    assert verdict.signal == "silent_zero"
    assert verdict.detail == "possible bot wall or markup change"


def test_silent_zero_is_suppressed_for_a_venue_with_no_baseline():
    """A venue that has never shown events (a legitimately quiet venue, or one too
    new to have a track record) must not be flagged for its natural silence."""
    logs = _attempts_every(6, CONSECUTIVE_WINDOW, status="success", events_found=0)
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.status == "ok"


def test_silent_zero_baseline_outside_the_30_day_window_does_not_count():
    recent_zero = _attempts_every(6, CONSECUTIVE_WINDOW, status="success", events_found=0)
    stale_baseline = _Log(
        started_at=NOW - timedelta(days=BASELINE_WINDOW_DAYS + 1), status="success", events_found=8
    )
    verdict = evaluate_venue_health(
        _Venue(), recent_zero + [stale_baseline], now=NOW, evaluate_staleness=False
    )
    assert verdict.status == "ok"


def test_silent_zero_baseline_exactly_at_the_window_edge_counts():
    recent_zero = _attempts_every(6, CONSECUTIVE_WINDOW, status="success", events_found=0)
    edge_baseline = _Log(
        started_at=NOW - timedelta(days=BASELINE_WINDOW_DAYS), status="success", events_found=8
    )
    verdict = evaluate_venue_health(
        _Venue(), recent_zero + [edge_baseline], now=NOW, evaluate_staleness=False
    )
    assert verdict.status == "warning"


# --- signal 3: staleness ---


def test_staleness_reports_critical_when_no_attempt_within_2x_max_gap():
    # "indie" group's max gap is 12h (app.cadence); 2x = 24h threshold.
    logs = [_Log(started_at=NOW - timedelta(hours=25), status="success", events_found=4)]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=True)
    assert verdict.status == "critical"
    assert verdict.signal == "stale"
    assert verdict.detail == "venue not being scraped at all"


def test_staleness_does_not_fire_within_the_threshold():
    logs = [_Log(started_at=NOW - timedelta(hours=23), status="success", events_found=4)]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=True)
    assert verdict.status == "ok"


def test_staleness_is_skipped_when_evaluate_staleness_is_false():
    logs = [_Log(started_at=NOW - timedelta(days=10), status="success", events_found=4)]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.status == "ok"


def test_staleness_threshold_follows_the_venues_group():
    # ticketmaster's max gap is also 12h in the shipped cadence table, but this pins
    # that the evaluator actually asks app.cadence per-venue rather than hardcoding
    # a single number, by using a venue whose scraper_type differs from the default
    # fixture and confirming the same 24h threshold still applies to it.
    tm_venue = _Venue(slug="dpac", scraper_type="ticketmaster")
    logs = [_Log(started_at=NOW - timedelta(hours=25), status="success", events_found=4)]
    verdict = evaluate_venue_health(tm_venue, logs, now=NOW, evaluate_staleness=True)
    assert verdict.status == "critical"
    assert verdict.signal == "stale"


# --- signal precedence (overlapping conditions; the first match wins) ---


def test_staleness_outranks_silent_zero():
    """A venue dropped from the schedule keeps whatever its last attempts looked like.
    When those were zero-event successes, both conditions hold -- and reporting the
    warning would tell an operator triaging by severity to deprioritize the venue that
    has actually vanished."""
    old = NOW - timedelta(days=5)
    logs = [
        _Log(started_at=old - timedelta(hours=6 * i), status="success", events_found=0)
        for i in range(CONSECUTIVE_WINDOW)
    ]
    logs.append(_Log(started_at=NOW - timedelta(days=10), status="success", events_found=7))
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=True)
    assert verdict.status == "critical"
    assert verdict.signal == "stale"


def test_staleness_outranks_consecutive_failures():
    """Both are critical, so only the triage text differs -- but "not being scraped at
    all" is the root cause, and leading with the last stored error would point the
    operator at the venue's site when the scheduler is what broke."""
    old = NOW - timedelta(days=5)
    logs = [
        _Log(started_at=old - timedelta(hours=6 * i), status="failed", error_message="boom")
        for i in range(CONSECUTIVE_WINDOW)
    ]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=True)
    assert verdict.signal == "stale"
    assert verdict.detail == "venue not being scraped at all"


def test_silent_zero_still_wins_when_the_venue_is_being_scraped_on_time():
    """The precedence above must not swallow silent-zero for a venue that is scraped
    on schedule -- that is the signal's whole purpose."""
    logs = _attempts_every(6, CONSECUTIVE_WINDOW, status="success", events_found=0)
    logs.append(_Log(started_at=NOW - timedelta(days=10), status="success", events_found=7))
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=True)
    assert verdict.status == "warning"
    assert verdict.signal == "silent_zero"


# --- last_success_at over a nullable finished_at ---


def test_last_success_at_skips_a_success_row_with_no_finished_at():
    """finished_at is nullable. Yielding the newest success's None would report "never
    succeeded" while an older success carrying a real timestamp sits in the same
    history."""
    stamped = _Log(
        started_at=NOW - timedelta(hours=12),
        status="success",
        events_found=4,
        finished_at=NOW - timedelta(hours=12) + timedelta(seconds=5),
    )
    unstamped = _Log(started_at=NOW - timedelta(hours=6), status="success", events_found=4)
    unstamped.finished_at = None  # bypass __post_init__'s default stamping
    verdict = evaluate_venue_health(
        _Venue(), [unstamped, stamped], now=NOW, evaluate_staleness=False
    )
    assert verdict.last_success_at == stamped.finished_at


# --- healthy path ---


def test_a_healthy_history_reports_ok():
    logs = _attempts_every(6, 5, status="success", events_found=6)
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=True)
    assert verdict == ScrapeHealthVerdict(
        venue_slug="cats-cradle",
        status="ok",
        signal=None,
        detail=None,
        last_success_at=logs[0].finished_at,
        last_attempt_at=logs[0].started_at,
    )


def test_last_success_and_last_attempt_reflect_the_full_visible_history():
    """last_success_at/last_attempt_at must not be limited to the 3-row consecutive
    window -- they describe the whole visible history."""
    logs = [
        _Log(started_at=NOW, status="failed", error_message="boom"),
        _Log(started_at=NOW - timedelta(hours=6), status="failed", error_message="boom"),
        _Log(
            started_at=NOW - timedelta(days=5),
            status="success",
            events_found=3,
            finished_at=NOW - timedelta(days=5) + timedelta(seconds=5),
        ),
    ]
    verdict = evaluate_venue_health(_Venue(), logs, now=NOW, evaluate_staleness=False)
    assert verdict.last_attempt_at == NOW
    assert verdict.last_success_at == logs[2].finished_at


# --- scrub_error_message ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", ""),
        ("no query string here", "no query string here"),
        (
            "Client error '401 Unauthorized' for url 'https://app.ticketmaster.com/discovery/v2/events.json?apikey=SECRET&venueId=abc'",
            "Client error '401 Unauthorized' for url 'https://app.ticketmaster.com/discovery/v2/events.json",
        ),
        # Truncated mid-query-string (error_message is stored as str(e)[:2000]) --
        # no closing quote or whitespace to anchor on, must still strip to EOS.
        (
            "https://app.ticketmaster.com/discovery/v2/events.json?apikey=SECRET12",
            "https://app.ticketmaster.com/discovery/v2/events.json",
        ),
        # Trailing context after the URL (separated by whitespace) must survive.
        (
            "request to https://x.example.com/foo?apikey=SECRET failed with 403",
            "request to https://x.example.com/foo failed with 403",
        ),
    ],
)
def test_scrub_error_message_strips_query_string_to_whitespace_or_end_of_string(raw, expected):
    assert scrub_error_message(raw) == expected
