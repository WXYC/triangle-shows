"""Tests for the surface-neutral /api/v1 API."""

from datetime import date

D = date(2026, 8, 1)


async def test_v1_events_returns_neutral_shape(client, make_event):
    await make_event(artist="Juana Molina", date=D, price_min=20.0, price_max=25.0)
    data = (await client.get("/api/v1/events")).json()
    assert len(data) == 1
    ev = data[0]
    # Neutral resource: typed fields, no FullCalendar presentation baked in.
    assert ev["artist"] == "Juana Molina"
    assert ev["price_min"] == 20.0
    assert ev["price_max"] == 25.0
    assert ev["updated_at"] is not None
    assert "venue_name" in ev
    for presentation_key in ("backgroundColor", "borderColor", "extendedProps", "title", "allDay"):
        assert presentation_key not in ev


async def test_v1_events_dedups_cross_venue(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    await make_event(venue=v1, artist="Duke Ellington", date=D)
    await make_event(
        venue=v2, artist="Duke Ellington", date=D,
        ticket_url="https://tix", image_url="https://img", price_min=10.0,
    )
    data = (await client.get("/api/v1/events")).json()
    assert len(data) == 1
    assert data[0]["venue_slug"] == "local-506"


async def test_v1_events_filters(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle", city="Carrboro")
    v2 = await make_venue(slug="local-506", city="Chapel Hill")
    await make_event(venue=v1, artist="Juana Molina", date=D)
    await make_event(venue=v2, artist="Jessica Pratt", date=D)
    assert [e["artist"] for e in (await client.get("/api/v1/events?city=Carrboro")).json()] == ["Juana Molina"]
    assert [e["artist"] for e in (await client.get("/api/v1/events?venue=local-506")).json()] == ["Jessica Pratt"]
    assert [e["artist"] for e in (await client.get("/api/v1/events?search=pratt")).json()] == ["Jessica Pratt"]


async def test_v1_event_detail_and_404(client, make_event):
    e = await make_event(artist="Cat Power", date=D)
    ok = await client.get(f"/api/v1/events/{e.id}")
    assert ok.status_code == 200
    assert ok.json()["artist"] == "Cat Power"
    assert (await client.get("/api/v1/events/999999")).status_code == 404


async def test_v1_venues_ordered_by_city(client, make_venue):
    await make_venue(slug="local-506", city="Chapel Hill", name="Local 506")
    await make_venue(slug="cats-cradle", city="Carrboro", name="Cat's Cradle")
    data = (await client.get("/api/v1/venues")).json()
    assert {v["slug"] for v in data} == {"cats-cradle", "local-506"}
    assert [v["city"] for v in data] == ["Carrboro", "Chapel Hill"]


async def test_v1_health(client, make_event):
    await make_event(artist="Stereolab", date=D)
    body = (await client.get("/api/v1/health")).json()
    assert body["status"] == "ok"
    assert body["event_count"] == 1
    assert body["venue_count"] == 1
