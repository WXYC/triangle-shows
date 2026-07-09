"""Integration tests for the deprecated /api/events aliases and the iCal feed.

The FullCalendar-shaped feed that once lived at /api/events/fullcalendar has been
removed — the web client now builds that shape itself (frontend/js/fullcalendar-adapter.js),
covered by the neutral /api/v1 tests. These guard the surviving deprecated aliases (the
paginated list now de-duplicates via the shared query service and keeps its historical
date leniency) and the iCal feed's distinct un-deduped contract.
"""

from conftest import DEFAULT_EVENT_DATE as D  # shared with the make_event factory default


async def test_list_events_dedups_and_reports_deduped_total(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    await make_event(venue=v1, artist="Duke Ellington", date=D)
    await make_event(venue=v2, artist="Duke Ellington", date=D)  # cross-venue duplicate
    await make_event(venue=v1, artist="Stereolab", date=D)
    body = (await client.get("/api/events?per_page=50")).json()
    assert body["total"] == 2           # 3 rows, one duplicate pair collapses
    assert len(body["events"]) == 2


async def test_list_events_tolerates_malformed_dates(client, make_event):
    # The deprecated list keeps its historical leniency: invalid date params are treated
    # as "no filter" so a malformed calendar request still renders (unlike /api/v1/events,
    # which rejects malformed dates with a 422).
    await make_event(artist="Juana Molina", date=D)
    resp = await client.get("/api/events?start=not-a-date&end=07/31/2026")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


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
