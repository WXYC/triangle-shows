"""Tests for GET /api/v1/health/scrapers (issue #86, part 1: detection and exposure).

The evaluator itself (app.services.scrape_health) is unit-tested in isolation in
tests/test_scrape_health.py against fixed `now` instants; these tests exercise the
endpoint's own responsibilities instead: wiring real ORM rows through the evaluator,
UTCDateTime serialization, the unauthenticated-endpoint redaction contract, and the
evaluate_staleness=settings.ENABLE_SCHEDULER wiring.
"""

from datetime import datetime, timedelta

from app.models import ScrapeLog


async def test_returns_empty_list_on_an_empty_database(client):
    resp = await client.get("/api/v1/health/scrapers")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_a_never_scraped_venue_reports_unknown(client, make_venue):
    venue = await make_venue(slug="never-scraped")
    resp = await client.get("/api/v1/health/scrapers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["venue_slug"] == "never-scraped"
    assert body[0]["status"] == "unknown"
    assert body[0]["signal"] is None
    assert body[0]["last_attempt_at"] is None
    assert body[0]["last_success_at"] is None


async def test_a_healthy_venue_reports_ok(client, session, make_venue):
    venue = await make_venue(slug="healthy-venue")
    now = datetime.utcnow()
    for i in range(5):
        session.add(
            ScrapeLog(
                venue_id=venue.id,
                scraper_type=venue.scraper_type,
                status="success",
                events_found=4,
                started_at=now - timedelta(hours=6 * i),
                finished_at=now - timedelta(hours=6 * i) + timedelta(seconds=5),
            )
        )
    await session.commit()

    resp = await client.get("/api/v1/health/scrapers")
    body = next(v for v in resp.json() if v["venue_slug"] == "healthy-venue")
    assert body["status"] == "ok"
    assert body["signal"] is None
    assert body["last_attempt_at"] is not None
    assert body["last_success_at"] is not None


async def test_timestamps_are_serialized_with_an_explicit_utc_offset(client, session, make_venue):
    venue = await make_venue(slug="offset-check")
    now = datetime.utcnow()
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type=venue.scraper_type,
            status="success",
            events_found=2,
            started_at=now,
            finished_at=now,
        )
    )
    await session.commit()

    resp = await client.get("/api/v1/health/scrapers")
    body = next(v for v in resp.json() if v["venue_slug"] == "offset-check")
    assert body["last_attempt_at"].endswith(("Z", "+00:00"))
    assert body["last_success_at"].endswith(("Z", "+00:00"))


async def test_consecutive_failures_report_critical_with_a_scrubbed_detail(client, session, make_venue):
    """Raw error_message must never leave the read boundary unredacted -- this is an
    unauthenticated endpoint, and ScrapeLog.error_message can embed the Ticketmaster
    API key as a query parameter."""
    venue = await make_venue(slug="broken-venue", scraper_type="ticketmaster")
    now = datetime.utcnow()
    credential_url = (
        "Client error '401 Unauthorized' for url "
        "'https://app.ticketmaster.com/discovery/v2/events.json?apikey=LIVESECRETVALUE&city=Raleigh'"
    )
    for i in range(3):
        session.add(
            ScrapeLog(
                venue_id=venue.id,
                scraper_type=venue.scraper_type,
                status="failed",
                error_message=credential_url,
                started_at=now - timedelta(hours=6 * i),
                finished_at=now - timedelta(hours=6 * i) + timedelta(seconds=1),
            )
        )
    await session.commit()

    resp = await client.get("/api/v1/health/scrapers")
    body = next(v for v in resp.json() if v["venue_slug"] == "broken-venue")
    assert body["status"] == "critical"
    assert body["signal"] == "consecutive_failures"
    assert "LIVESECRETVALUE" not in body["detail"]
    assert "apikey" not in body["detail"]
    assert "LIVESECRETVALUE" not in resp.text


async def test_scraper_type_is_not_in_the_response(client, make_venue):
    await make_venue(slug="no-scraper-type-leak", scraper_type="ticketmaster")
    resp = await client.get("/api/v1/health/scrapers")
    body = next(v for v in resp.json() if v["venue_slug"] == "no-scraper-type-leak")
    assert "scraper_type" not in body


async def test_staleness_is_not_evaluated_when_the_scheduler_is_disabled(client, session, make_venue, monkeypatch):
    """settings.ENABLE_SCHEDULER defaults to False (dev/tests/a region not yet
    switched on); a venue whose only scrape is long in the past must not read
    "critical" purely because nothing was ever scheduled to keep it fresh."""
    import app.api.v1 as v1_module

    assert v1_module.settings.ENABLE_SCHEDULER is False

    venue = await make_venue(slug="old-but-fine")
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type=venue.scraper_type,
            status="success",
            events_found=2,
            started_at=datetime.utcnow() - timedelta(days=10),
            finished_at=datetime.utcnow() - timedelta(days=10),
        )
    )
    await session.commit()

    resp = await client.get("/api/v1/health/scrapers")
    body = next(v for v in resp.json() if v["venue_slug"] == "old-but-fine")
    assert body["status"] == "ok"


async def test_a_venue_stale_far_beyond_the_baseline_window_still_reports_stale(
    client, session, make_venue, monkeypatch
):
    """A venue whose last (and only) attempt is far older than the 30-day baseline
    window must still resolve its true last attempt -- ORDER BY started_at DESC
    always puts the true most-recent row first regardless of age, so a long-dead
    venue reads "critical"/"stale", not the misleading "unknown" a naive
    time-windowed query would produce."""
    from app.config import settings

    monkeypatch.setattr(settings, "ENABLE_SCHEDULER", True)

    venue = await make_venue(slug="long-dead", scraper_type="ticketmaster")
    old = datetime.utcnow() - timedelta(days=60)
    session.add(
        ScrapeLog(
            venue_id=venue.id,
            scraper_type=venue.scraper_type,
            status="success",
            events_found=3,
            started_at=old,
            finished_at=old,
        )
    )
    await session.commit()

    resp = await client.get("/api/v1/health/scrapers")
    body = next(v for v in resp.json() if v["venue_slug"] == "long-dead")
    assert body["status"] == "critical"
    assert body["signal"] == "stale"
