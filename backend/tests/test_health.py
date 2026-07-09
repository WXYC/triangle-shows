"""Smoke test: the health endpoint responds against a freshly-created empty database.

This exercises the whole harness end to end — test DB creation, schema create_all,
the app fixture, and the get_session override — so a green run here means the rest
of the suite has a working foundation.
"""


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
