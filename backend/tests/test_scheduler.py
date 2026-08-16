"""Tests for app.scheduler's cron configuration (region-pack epic Phase 2, issue #64)
and its job-event listener (issue #118).

Pins that the scheduled jobs' timezone comes from the active region's site.toml
(site.timezone) rather than the former "US/Eastern" literal — same zone for
Triangle (the alias converges to its canonical IANA form, behavior-identical),
but now configurable per region.
"""

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.triggers.cron import CronTrigger

from app import scheduler as scheduler_module
from app.scheduler import _job_listener, configure_scheduler, scheduler

# The job ids configure_scheduler() registers, and the set every test's cleanup is
# derived from — so the assertion and the teardown can never drift apart.
JOB_IDS = {"scrape_ticketmaster", "scrape_indie", "cleanup_past_events"}


def _tz_str(trigger: CronTrigger) -> str:
    return str(trigger.timezone)


def _cleanup():
    for job_id in JOB_IDS:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    scheduler.remove_listener(_job_listener)


def test_configured_jobs_use_the_site_configured_timezone():
    configure_scheduler()
    try:
        jobs = {job.id: job for job in scheduler.get_jobs() if job.id in JOB_IDS}
        assert JOB_IDS == set(jobs)
        for job in jobs.values():
            # Triangle's shipped pack pins America/New_York — the canonical IANA
            # form of the historical "US/Eastern" literal (same wall-clock zone).
            assert _tz_str(job.trigger) == "America/New_York"
    finally:
        _cleanup()


def test_configure_scheduler_registers_the_job_listener():
    configure_scheduler()
    try:
        assert any(cb == _job_listener for cb, _mask in scheduler._listeners)
    finally:
        _cleanup()


def test_configure_scheduler_is_idempotent_for_the_listener():
    """add_listener has no built-in dedupe, unlike add_job's replace_existing=True
    --- so calling configure_scheduler() twice (hot reload, or two tests in the same
    process) must still leave exactly one registration, or "exactly one
    report_error" assertions become order- and xdist-shard-dependent."""
    configure_scheduler()
    configure_scheduler()
    try:
        matches = [cb for cb, _mask in scheduler._listeners if cb == _job_listener]
        assert len(matches) == 1
    finally:
        _cleanup()


def test_a_raising_job_reaches_report_error(monkeypatch):
    reported = []
    monkeypatch.setattr(scheduler_module, "report_error", lambda exc, **kw: reported.append((exc, kw)))

    exc = RuntimeError("job blew up")
    event = JobExecutionEvent(EVENT_JOB_ERROR, "scrape_ticketmaster", "default", None, exception=exc)

    _job_listener(event)

    assert len(reported) == 1
    forwarded_exc, kwargs = reported[0]
    assert forwarded_exc is exc
    assert kwargs["where"] == "scheduler.job_error"
    assert kwargs["context"] == {"job_id": "scrape_ticketmaster"}


def test_a_missed_job_takes_the_warning_path_and_never_calls_report_error(monkeypatch, caplog):
    """apscheduler/executors/base.py builds the missed-job event with exception and
    traceback both defaulting to None — routing that into report_error would call
    capture_exception(None), which falls back to sys.exc_info() and can misattribute
    an unrelated in-flight exception to a missed job."""
    import logging

    caplog.set_level(logging.WARNING, logger="app.scheduler")
    reported = []
    monkeypatch.setattr(scheduler_module, "report_error", lambda exc, **kw: reported.append((exc, kw)))

    event = JobExecutionEvent(EVENT_JOB_MISSED, "scrape_indie", "default", None)

    _job_listener(event)

    assert reported == []
    assert "scrape_indie" in caplog.text
