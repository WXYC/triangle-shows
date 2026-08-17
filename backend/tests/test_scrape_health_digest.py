"""Tests for the scrape-health digest job (issue #86 part 2: alerting).

The evaluator itself (app.services.scrape_health) is unit-tested in isolation in
test_scrape_health.py; these tests exercise the digest job's own responsibility --
turning two evaluator calls (``now`` and ``now - 24h``, the stateless replay) into
transition-only alerts. Only a status change between "broken" (warning/critical)
and "not broken" (ok) should ever call app.observability.send_alert -- a venue
that has been broken (or healthy) across both instants must stay silent.

Follows test_vanished_events.py's seam: the job's own async_session is
monkeypatched to the test's sessionmaker, since the module-global engine's pool
is bound to the production event loop.
"""

import logging
from datetime import datetime, timedelta

from app import scheduler as scheduler_module
from app.models import ScrapeLog
from app.scheduler import scrape_health_digest_job

# Every event in this file is anchored to a single instant, matching the
# suite-wide rule against ambient-clock tests (see test_scrape_health.py's own
# NOW pin, and commit 6357814): fetched once and passed to every helper below,
# never re-read from datetime.utcnow() mid-test.
NOW = datetime.utcnow()


async def _log(session, venue, *, hours_ago, status, events_found=0, error_message=None):
    started = NOW - timedelta(hours=hours_ago)
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type=venue.scraper_type,
            status=status,
            events_found=events_found,
            error_message=error_message,
            started_at=started,
            finished_at=started + timedelta(seconds=5),
        )
    )


def _capturing_send_alert(sink):
    """Builds an async stand-in for observability.send_alert that appends its
    argument to `sink` -- send_alert is async, so a plain (sync) lambda can't
    stand in for it: `await <lambda>(...)` fails because calling a lambda
    returns None, not a coroutine."""

    async def _send(text):
        sink.append(text)

    return _send


async def test_fires_on_break(session, make_venue, monkeypatch, _sessionmaker):
    """A venue that was healthy as of 24h ago and has since failed three times in
    a row must produce exactly one send_alert call describing the break."""
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    venue = await make_venue(slug="just-broke", scraper_type="ticketmaster")
    # Healthy history, all older than 24h -- this is what the "24h ago" replay sees.
    for hours_ago in (48, 42, 36):
        await _log(session, venue, hours_ago=hours_ago, status="success", events_found=5)
    # Three straight failures inside the last 24h -- invisible to the replay at
    # now - 24h, but the newest attempts as of `now`.
    for hours_ago in (6, 4, 2):
        await _log(
            session, venue, hours_ago=hours_ago, status="failed",
            error_message="Client error for url 'https://example.com/x?apikey=SECRETVALUE'",
        )
    await session.commit()

    sent = []
    monkeypatch.setattr(scheduler_module, "send_alert", _capturing_send_alert(sent))

    await scrape_health_digest_job()

    assert len(sent) == 1
    text = sent[0]
    assert "Triangle Shows" in text
    assert "just-broke" in text
    assert "consecutive_failures" in text
    # The evaluator's own scrub must have already run -- the digest is a consumer,
    # never a second sanitization layer.
    assert "SECRETVALUE" not in text


async def test_silence_while_still_broken(session, make_venue, monkeypatch, _sessionmaker):
    """A venue that was already broken 24h ago and is still broken now must not
    re-alert -- transition-only means no daily repeat while nothing changed."""
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    venue = await make_venue(slug="still-broken", scraper_type="ticketmaster")
    # Eight failures spread across 48h, one every 6h: the most-recent three
    # visible as of `now - 24h` (hours_ago 28/34/40) and the most-recent three
    # visible as of `now` (hours_ago 4/10/16) are both all-failed, so both
    # replay instants read "critical".
    for hours_ago in (46, 40, 34, 28, 22, 16, 10, 4):
        await _log(session, venue, hours_ago=hours_ago, status="failed", error_message="boom")
    await session.commit()

    sent = []
    monkeypatch.setattr(scheduler_module, "send_alert", _capturing_send_alert(sent))

    await scrape_health_digest_job()

    assert sent == []


async def test_fires_on_recover(session, make_venue, monkeypatch, _sessionmaker):
    """A venue that was broken 24h ago and has since recovered must produce
    exactly one send_alert call describing the recovery."""
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    venue = await make_venue(slug="just-recovered", scraper_type="ticketmaster")
    # Three failures, all older than 24h -- what the replay at now - 24h sees.
    for hours_ago in (46, 40, 34):
        await _log(session, venue, hours_ago=hours_ago, status="failed", error_message="boom")
    # Three healthy scrapes inside the last 24h -- invisible to the replay,
    # the newest attempts as of `now`.
    for hours_ago in (6, 4, 2):
        await _log(session, venue, hours_ago=hours_ago, status="success", events_found=5)
    await session.commit()

    sent = []
    monkeypatch.setattr(scheduler_module, "send_alert", _capturing_send_alert(sent))

    await scrape_health_digest_job()

    assert len(sent) == 1
    text = sent[0]
    assert "Triangle Shows" in text
    assert "just-recovered" in text
    assert "RECOVERED" in text.upper()


async def test_no_transitions_means_no_alert_at_all(session, make_venue, monkeypatch, _sessionmaker):
    """A venue that has been healthy across both replay instants is the ordinary
    steady state -- most days, most venues -- and must never call send_alert."""
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    venue = await make_venue(slug="always-fine", scraper_type="ticketmaster")
    for hours_ago in (48, 30, 6):
        await _log(session, venue, hours_ago=hours_ago, status="success", events_found=5)
    await session.commit()

    sent = []
    monkeypatch.setattr(scheduler_module, "send_alert", _capturing_send_alert(sent))

    await scrape_health_digest_job()

    assert sent == []


async def test_a_never_scraped_venue_is_silent(session, make_venue, monkeypatch, _sessionmaker):
    """A venue with no ScrapeLog rows at all evaluates to `unknown`, which the digest
    groups with `ok` on the not-broken side -- never-scraped is not broken. Without
    this, adding a venue to venues.toml would page once on its own, in the window
    between the row existing and its first scrape finishing."""
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    await make_venue(slug="brand-new", scraper_type="ticketmaster")
    await session.commit()

    sent = []
    monkeypatch.setattr(scheduler_module, "send_alert", _capturing_send_alert(sent))

    await scrape_health_digest_job()

    assert sent == []


async def test_a_venue_whose_first_scrapes_all_fail_still_alerts(session, make_venue, monkeypatch, _sessionmaker):
    """The other half of the `unknown` rule: treating never-scraped as not-broken must
    not also silence a venue that has been scraped and is failing. Its attempts are
    visible rows, so it crosses unknown -> critical as soon as the streak fills the
    consecutive-failure window, and that crossing is a transition like any other."""
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    venue = await make_venue(slug="broken-from-birth", scraper_type="ticketmaster")
    # The venue's entire history, all of it inside the last 24h -- so the replay at
    # now - 24h sees no rows at all and reads `unknown`.
    for hours_ago in (6, 4, 2):
        await _log(session, venue, hours_ago=hours_ago, status="failed", error_message="boom")
    await session.commit()

    sent = []
    monkeypatch.setattr(scheduler_module, "send_alert", _capturing_send_alert(sent))

    await scrape_health_digest_job()

    assert len(sent) == 1
    assert "broken-from-birth" in sent[0]
    assert "consecutive_failures" in sent[0]


async def test_webhook_unset_logs_the_digest_instead(session, make_venue, monkeypatch, _sessionmaker, caplog):
    """With ALERT_WEBHOOK_URL unset (the test-suite default), the real
    observability.send_alert path logs the digest text rather than posting it --
    and, unlike before issue #101 closed, that log line is now genuinely
    observable instead of vanishing into a suppressed logger."""
    monkeypatch.setattr("app.scheduler.async_session", _sessionmaker)
    caplog.set_level(logging.INFO, logger="app.observability")

    venue = await make_venue(slug="unwired-webhook", scraper_type="ticketmaster")
    for hours_ago in (48, 42, 36):
        await _log(session, venue, hours_ago=hours_ago, status="success", events_found=5)
    for hours_ago in (6, 4, 2):
        await _log(session, venue, hours_ago=hours_ago, status="failed", error_message="boom")
    await session.commit()

    await scrape_health_digest_job()  # real send_alert, not mocked

    assert "Triangle Shows" in caplog.text
    assert "unwired-webhook" in caplog.text
