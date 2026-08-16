"""Smoke test: the health endpoint responds against a freshly-created empty database.

This exercises the whole harness end to end — test DB creation, schema create_all,
the app fixture, and the get_session override — so a green run here means the rest
of the suite has a working foundation.
"""

from datetime import datetime

from app.models import ScrapeLog


async def test_health_on_empty_database(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "ok"
    assert body["event_count"] == 0
    assert body["venue_count"] == 0
    assert body["last_scrape"] is None


async def test_factories_persist_rows_visible_to_the_api(client, make_event):
    """A row inserted via the ORM factory is visible through an API request."""
    await make_event(artist="Juana Molina")

    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_count"] == 1
    assert body["venue_count"] == 1


async def test_health_last_scrape_carries_utc_offset(client, session, make_venue):
    """last_scrape is stored as naive UTC; the API must serialize it with an explicit offset."""
    venue = await make_venue()
    session.add(ScrapeLog(venue_id=venue.id, scraper_type="manual", status="success", finished_at=datetime.utcnow()))
    await session.commit()

    body = (await client.get("/api/v1/health")).json()
    assert body["last_scrape"] is not None
    assert body["last_scrape"].endswith(("Z", "+00:00"))


async def test_health_version_reports_unknown_when_git_commit_is_absent(client, monkeypatch):
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    body = (await client.get("/api/health")).json()
    assert body["version"] == "unknown"


async def test_health_version_reports_unknown_not_empty_string_when_git_commit_is_set_but_empty(
    client, monkeypatch
):
    """A Railway reference variable (GIT_COMMIT=${{RAILWAY_GIT_COMMIT_SHA}}) that fails
    to resolve is *set but empty*, not absent — os.environ.get(k, default) only falls
    back to the default when the key is missing entirely, so this must be handled
    explicitly or health silently regresses from an honest "unknown" to "".
    """
    monkeypatch.setenv("GIT_COMMIT", "")
    body = (await client.get("/api/health")).json()
    assert body["version"] == "unknown"


async def test_health_version_reports_the_configured_git_commit(client, monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    body = (await client.get("/api/health")).json()
    assert body["version"] == "abc1234"
