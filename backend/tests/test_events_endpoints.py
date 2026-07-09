"""Integration tests for the /api/events endpoints after the shared-query refactor.

Guards that the (deprecated) FullCalendar feed keeps its exact shape and de-dup
behavior, and that the paginated list now de-duplicates via the same service.
"""

from datetime import time

from conftest import DEFAULT_EVENT_DATE as D  # shared with the make_event factory default


async def test_fullcalendar_shape_and_cross_venue_dedup(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle", color="#111111")
    v2 = await make_venue(slug="local-506", color="#222222")
    await make_event(venue=v1, artist="Juana Molina", date=D)  # sparse, first
    await make_event(
        venue=v2, artist="Juana Molina", date=D,
        image_url="https://img", ticket_url="https://tix", price_min=20.0, price_max=25.0,
    )
    resp = await client.get("/api/events/fullcalendar")
    assert resp.status_code == 200
    data = resp.json()
    # The cross-venue duplicate collapses to the richer (v2) record.
    assert len(data) == 1
    ev = data[0]
    assert ev["title"] == "Juana Molina"
    assert ev["allDay"] is True
    assert ev["backgroundColor"] == "#222222"
    assert ev["extendedProps"]["venue_slug"] == "local-506"
    assert ev["extendedProps"]["price"] == "$20-$25"


async def test_fullcalendar_formats_times_without_leading_zero(client, make_event):
    await make_event(artist="Jessica Pratt", date=D, show_time=time(20, 0), doors_time=time(19, 0))
    ev = (await client.get("/api/events/fullcalendar")).json()[0]
    assert ev["extendedProps"]["show_time"] == "8:00 PM"
    assert ev["extendedProps"]["doors_time"] == "7:00 PM"


async def test_fullcalendar_tolerates_malformed_dates(client, make_event):
    # The deprecated feed keeps its historical leniency: invalid date params are
    # treated as "no filter" so the calendar still renders.
    await make_event(artist="Juana Molina", date=D)
    resp = await client.get("/api/events/fullcalendar?start=not-a-date&end=07/31/2026")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_fullcalendar_empty_filter_value_matches_nothing(client, make_event):
    # A filter that is present but selects nothing (e.g. "?venue=,,") must return an
    # empty set — the historical behavior — not silently drop the filter and return
    # every event.
    await make_event(artist="Juana Molina", date=D)
    assert (await client.get("/api/events/fullcalendar?venue=,,")).json() == []
    assert (await client.get("/api/events/fullcalendar?city=,")).json() == []


async def test_list_events_dedups_and_reports_deduped_total(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    await make_event(venue=v1, artist="Duke Ellington", date=D)
    await make_event(venue=v2, artist="Duke Ellington", date=D)  # cross-venue duplicate
    await make_event(venue=v1, artist="Stereolab", date=D)
    body = (await client.get("/api/events?per_page=50")).json()
    assert body["total"] == 2           # 3 rows, one duplicate pair collapses
    assert len(body["events"]) == 2


async def test_get_event_by_id_includes_updated_at_and_404s(client, make_event):
    e = await make_event(artist="Cat Power", date=D)
    ok = await client.get(f"/api/events/{e.id}")
    assert ok.status_code == 200
    assert ok.json()["artist"] == "Cat Power"
    assert ok.json()["updated_at"] is not None
    assert (await client.get("/api/events/999999")).status_code == 404


async def test_ical_feed_lists_every_venue_offering(client, make_venue, make_event):
    """The iCal feed uses the shared query service with dedup=False — cross-venue
    duplicate listings both appear, unlike the calendar surfaces."""
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    await make_event(venue=v1, artist="Duke Ellington", date=D)
    await make_event(venue=v2, artist="Duke Ellington", date=D)
    resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    assert resp.text.count("SUMMARY:Duke Ellington") == 2


async def test_ical_feed_filters_by_venue_slug(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    await make_event(venue=v1, artist="Juana Molina", date=D)
    await make_event(venue=v2, artist="Jessica Pratt", date=D)
    resp = await client.get("/feeds/events.ics?venue=cats-cradle")
    assert resp.status_code == 200
    assert "SUMMARY:Juana Molina" in resp.text
    assert "SUMMARY:Jessica Pratt" not in resp.text
