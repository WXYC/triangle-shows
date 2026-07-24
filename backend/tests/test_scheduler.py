"""Tests for app.scheduler's cron configuration (region-pack epic Phase 2, issue #64).

Pins that the scheduled jobs' timezone comes from the active region's site.toml
(site.timezone) rather than the former "US/Eastern" literal — same zone for
Triangle (the alias converges to its canonical IANA form, behavior-identical),
but now configurable per region.
"""

from apscheduler.triggers.cron import CronTrigger

from app.scheduler import configure_scheduler, scheduler


def _tz_str(trigger: CronTrigger) -> str:
    return str(trigger.timezone)


def test_configured_jobs_use_the_site_configured_timezone():
    configure_scheduler()
    try:
        job_ids = {"scrape_ticketmaster", "scrape_indie", "cleanup_past_events"}
        jobs = {job.id: job for job in scheduler.get_jobs() if job.id in job_ids}
        assert job_ids == set(jobs)
        for job in jobs.values():
            # Triangle's shipped pack pins America/New_York — the canonical IANA
            # form of the historical "US/Eastern" literal (same wall-clock zone).
            assert _tz_str(job.trigger) == "America/New_York"
    finally:
        for job_id in ("scrape_ticketmaster", "scrape_indie", "cleanup_past_events"):
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
