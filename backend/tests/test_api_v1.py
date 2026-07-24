"""Tests for the surface-neutral /api/v1 API."""

import contextlib
from datetime import date, datetime, timedelta

from conftest import DEFAULT_EVENT_DATE as D  # shared with the make_event factory default


async def test_v1_events_returns_neutral_shape(client, make_event):
    await make_event(artist="Juana Molina", date=D, price_min=20.0, price_max=25.0)
    data = (await client.get("/api/v1/events")).json()
    assert len(data) == 1
    ev = data[0]
    # Neutral resource: typed fields, no FullCalendar presentation baked in.
    assert ev["artist"] == "Juana Molina"
    assert ev["price_min"] == 20.0
    assert ev["price_max"] == 25.0
    assert "venue_name" in ev
    for presentation_key in ("backgroundColor", "borderColor", "extendedProps", "title", "allDay"):
        assert presentation_key not in ev


async def test_v1_events_expose_headliner_additively(client, make_event):
    billing = "Acid Mother's Temple w/ Magick Potion"
    await make_event(name=billing, artist=billing, headliner="Acid Mother's Temple", date=D)
    ev = (await client.get("/api/v1/events")).json()[0]
    # The cleaned performer is its own field; name and artist keep the full
    # billing byte-identical — additive, never a repurpose (issue #18).
    assert ev["headliner"] == "Acid Mother's Temple"
    assert ev["name"] == billing
    assert ev["artist"] == billing


async def test_v1_events_headliner_is_null_when_never_derived(client, make_event):
    # Rows that predate the field (or were never rescraped) carry null — the
    # field is documented best-effort and consumers fall back themselves.
    await make_event(artist="Juana Molina", date=D)
    ev = (await client.get("/api/v1/events")).json()[0]
    assert ev["headliner"] is None


async def test_v1_events_support_artists_serializes_as_array(client, make_event):
    # support_artists is a lossless list, serialized as a JSON array (issue #40) —
    # a name with an internal comma stays one element, never split into fake acts.
    await make_event(artist="Juana Molina", date=D, support_artists=["Truth Club", "Earth, Wind & Fire"])
    ev = (await client.get("/api/v1/events")).json()[0]
    assert ev["support_artists"] == ["Truth Club", "Earth, Wind & Fire"]


async def test_v1_events_support_artists_empty_is_array_not_null(client, make_event):
    # No support acts -> empty array, never null, so consumers can .map/.join safely.
    await make_event(artist="Jessica Pratt", date=D)
    ev = (await client.get("/api/v1/events")).json()[0]
    assert ev["support_artists"] == []


async def test_deprecated_events_support_artists_serializes_as_array(client, make_event):
    # The deprecated /api/events surface flips to the array shape too (issue #40) —
    # uniform with /api/v1/events, zero external consumers to protect.
    await make_event(artist="Cat Power", date=D, support_artists=["Support Act"])
    ev = (await client.get("/api/events")).json()["events"][0]
    assert ev["support_artists"] == ["Support Act"]


async def test_v1_event_detail_support_artists_serializes_as_array(client, make_event):
    e = await make_event(artist="Stereolab", date=D, support_artists=["Opener One", "Opener Two"])
    detail = (await client.get(f"/api/v1/events/{e.id}")).json()
    assert detail["support_artists"] == ["Opener One", "Opener Two"]


async def test_v1_events_updated_at_carries_utc_offset(client, make_event):
    await make_event(artist="Nilüfer Yanya", date=D)
    ev = (await client.get("/api/v1/events")).json()[0]
    # The sync timestamp must be unambiguous: serialized with an explicit UTC marker.
    assert ev["updated_at"] is not None
    assert ev["updated_at"].endswith(("Z", "+00:00"))


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


async def test_v1_events_dedup_can_be_disabled(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    await make_event(venue=v1, artist="Duke Ellington", date=D)
    await make_event(venue=v2, artist="Duke Ellington", date=D)
    data = (await client.get("/api/v1/events?dedup=false")).json()
    assert len(data) == 2


async def test_v1_events_window_defaults_to_upcoming(client, make_venue, make_event):
    v = await make_venue()
    past = date.today() - timedelta(days=10)
    await make_event(venue=v, artist="Stereolab", date=past)
    await make_event(venue=v, artist="Cat Power", date=D)
    # Without any bound, only upcoming events are returned — never the whole history.
    default_window = (await client.get("/api/v1/events")).json()
    assert [e["artist"] for e in default_window] == ["Cat Power"]
    # History remains reachable with an explicit start...
    explicit = (await client.get(f"/api/v1/events?start={past.isoformat()}")).json()
    assert [e["artist"] for e in explicit] == ["Stereolab", "Cat Power"]
    # ...and an end-only query means "everything up to end" — the start default must
    # not sneak in and make a historical window silently empty.
    end_only = (await client.get(f"/api/v1/events?end={date.today().isoformat()}")).json()
    assert [e["artist"] for e in end_only] == ["Stereolab"]


async def test_v1_events_rejects_malformed_query_params(client):
    # Malformed dates are a 422, not a silent full-table dump.
    assert (await client.get("/api/v1/events?start=07/01/2026")).status_code == 422
    assert (await client.get(f"/api/v1/events?start={D.isoformat()}&end=not-a-date")).status_code == 422
    # Unknown status values are rejected rather than matching zero rows.
    assert (await client.get("/api/v1/events?status=onsale")).status_code == 422
    assert (await client.get("/api/v1/events?status=on_sale")).status_code == 200


async def test_v1_events_filters(client, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle", city="Carrboro")
    v2 = await make_venue(slug="local-506", city="Chapel Hill")
    await make_event(venue=v1, artist="Juana Molina", date=D)
    await make_event(venue=v2, artist="Jessica Pratt", date=D)
    assert [e["artist"] for e in (await client.get("/api/v1/events?city=Carrboro")).json()] == ["Juana Molina"]
    assert [e["artist"] for e in (await client.get("/api/v1/events?venue=local-506")).json()] == ["Jessica Pratt"]
    assert [e["artist"] for e in (await client.get("/api/v1/events?search=pratt")).json()] == ["Jessica Pratt"]


async def test_v1_city_alias_expands_to_both_municipalities(client, make_venue, make_event):
    carrboro = await make_venue(slug="cats-cradle", city="Carrboro")
    chapel_hill = await make_venue(slug="local-506", city="Chapel Hill")
    durham = await make_venue(slug="the-pinhook", city="Durham")
    await make_event(venue=carrboro, artist="Juana Molina", date=D)
    await make_event(venue=chapel_hill, artist="Jessica Pratt", date=D)
    await make_event(venue=durham, artist="Chuquimamani-Condori", date=D)
    # "Chapel Hill-Carrboro" is a display grouping, not a stored city value; the v1
    # param keeps accepting it as an alias for both municipalities so old links work.
    data = (await client.get("/api/v1/events?city=Chapel Hill-Carrboro")).json()
    assert {e["artist"] for e in data} == {"Juana Molina", "Jessica Pratt"}
    # The param is comma-separated and the alias expands per token, so it composes
    # with plain municipalities.
    mixed = (await client.get("/api/v1/events?city=Chapel Hill-Carrboro,Durham")).json()
    assert {e["artist"] for e in mixed} == {"Juana Molina", "Jessica Pratt", "Chuquimamani-Condori"}


async def test_v1_empty_filter_value_matches_nothing(client, make_event):
    # A filter that is present but contains no usable segments (e.g. "?venue=,,") selects
    # nothing, rather than being silently dropped so every event is returned.
    await make_event(artist="Juana Molina", date=D)
    assert len((await client.get("/api/v1/events")).json()) == 1  # baseline: event is in-window
    assert (await client.get("/api/v1/events?venue=,,")).json() == []
    assert (await client.get("/api/v1/events?city=,")).json() == []


async def test_v1_event_detail_and_404(client, make_event):
    e = await make_event(artist="Cat Power", date=D)
    ok = await client.get(f"/api/v1/events/{e.id}")
    assert ok.status_code == 200
    assert ok.json()["artist"] == "Cat Power"
    assert (await client.get("/api/v1/events/999999")).status_code == 404
    # Ids beyond int4 must 422 at validation, not surface as a database error (500).
    assert (await client.get("/api/v1/events/99999999999999")).status_code == 422
    assert (await client.get("/api/events/99999999999999")).status_code == 422


async def test_v1_events_excludes_tombstoned_by_default(client, make_venue, make_event):
    v = await make_venue()
    await make_event(venue=v, artist="Juana Molina", date=D)
    await make_event(venue=v, artist="Jessica Pratt", date=D, removed_at=datetime.utcnow())
    data = (await client.get("/api/v1/events")).json()
    assert [e["artist"] for e in data] == ["Juana Molina"]


async def test_v1_events_include_removed_exposes_tombstones(client, make_venue, make_event):
    v = await make_venue()
    await make_event(venue=v, artist="Juana Molina", date=D)
    await make_event(venue=v, artist="Jessica Pratt", date=D, removed_at=datetime.utcnow())
    data = (await client.get("/api/v1/events?include_removed=true")).json()
    by_artist = {e["artist"]: e for e in data}
    assert set(by_artist) == {"Juana Molina", "Jessica Pratt"}
    # removed_at rides along: populated for tombstones (with an explicit UTC
    # marker, like every timestamp), null for live rows.
    assert by_artist["Jessica Pratt"]["removed_at"].endswith(("Z", "+00:00"))
    assert by_artist["Juana Molina"]["removed_at"] is None


async def test_v1_event_detail_returns_tombstoned_event_by_id(client, make_event):
    """Downstream consumers mirror-and-decide: a known id must stay resolvable
    after the venue delists the show."""
    e = await make_event(artist="Jessica Pratt", date=D, removed_at=datetime.utcnow())
    resp = await client.get(f"/api/v1/events/{e.id}")
    assert resp.status_code == 200
    assert resp.json()["removed_at"] is not None


async def test_v1_venues_ordered_by_city(client, make_venue):
    await make_venue(slug="local-506", city="Chapel Hill", name="Local 506")
    await make_venue(slug="cats-cradle", city="Carrboro", name="Cat's Cradle")
    data = (await client.get("/api/v1/venues")).json()
    assert {v["slug"] for v in data} == {"cats-cradle", "local-506"}
    assert [v["city"] for v in data] == ["Carrboro", "Chapel Hill"]
    # Internal scraping details stay out of the public contract.
    assert all("scraper_type" not in v for v in data)


# Characterization pin (region-pack epic, issue #62/#63): today's shipped venue
# roster, end to end through the real seed path and the v1 API. Must stay green
# unmodified through Phases 1-3.
async def test_v1_venues_returns_all_22_shipped_venues_with_real_municipalities(client, session, monkeypatch):
    from app.seed import seed_venues

    @contextlib.asynccontextmanager
    async def _session_cm():
        yield session

    async def _noop_init_db():
        pass

    monkeypatch.setattr("app.seed.init_db", _noop_init_db)
    monkeypatch.setattr("app.seed.async_session", _session_cm)
    await seed_venues()

    data = (await client.get("/api/v1/venues")).json()
    assert len(data) == 22
    assert {v["city"] for v in data} == {
        "Raleigh", "Durham", "Chapel Hill", "Carrboro", "Cary", "Saxapahaw",
    }


async def test_v1_health_matches_unversioned_alias(client, make_event):
    await make_event(artist="Stereolab", date=D)
    body = (await client.get("/api/v1/health")).json()
    assert body["status"] == "ok"
    assert body["event_count"] == 1
    assert body["venue_count"] == 1
    # v1 delegates to the same handler, so the two surfaces cannot drift.
    assert body == (await client.get("/api/health")).json()


async def test_v1_events_exposes_source_key(client, make_event):
    # source_key is the stable per-event identity consumers key on (issue #8) —
    # e.g. Backend-Service upserts concerts as (source='triangle_shows',
    # source_id=source_key). It must round-trip exactly as stored.
    await make_event(artist="Chuquimamani-Condori", date=D, source_key="url:/event/chuqui")
    ev = (await client.get("/api/v1/events")).json()[0]
    assert ev["source_key"] == "url:/event/chuqui"

    detail = (await client.get(f"/api/v1/events/{ev['id']}")).json()
    assert detail["source_key"] == "url:/event/chuqui"
