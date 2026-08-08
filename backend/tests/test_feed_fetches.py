"""Tests for server-side .ics feed telemetry (issue #88).

Every successfully served GET /feeds/events.ics records one append-only row in
feed_fetches, keyed by a salted, truncated hash of (client IP, user agent) —
never the raw values. The write is best-effort: telemetry failures must never
break the feed response. See app/api/feeds.py::record_feed_fetch.
"""

from sqlalchemy import func, select

from app.config import settings
from app.models import FeedFetch


async def test_get_ical_feed_inserts_exactly_one_feed_fetch_row(client, session):
    resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200

    count = (await session.execute(select(func.count(FeedFetch.id)))).scalar()
    assert count == 1


async def test_client_hash_is_16_lowercase_hex_chars(client, session):
    await client.get("/feeds/events.ics", headers={"User-Agent": "TestPoller/1.0"})

    row = (await session.execute(select(FeedFetch))).scalar_one()
    assert len(row.client_hash) == 16
    assert row.client_hash == row.client_hash.lower()
    assert all(c in "0123456789abcdef" for c in row.client_hash)


async def test_client_hash_is_stable_for_identical_ip_and_user_agent(client, session):
    headers = {"User-Agent": "TestPoller/1.0", "X-Forwarded-For": "5.5.5.5"}
    await client.get("/feeds/events.ics", headers=headers)
    await client.get("/feeds/events.ics", headers=headers)

    rows = (await session.execute(select(FeedFetch).order_by(FeedFetch.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].client_hash == rows[1].client_hash


async def test_client_hash_differs_for_a_different_user_agent(client, session):
    common_headers = {"X-Forwarded-For": "5.5.5.5"}
    await client.get("/feeds/events.ics", headers={**common_headers, "User-Agent": "PollerA/1.0"})
    await client.get("/feeds/events.ics", headers={**common_headers, "User-Agent": "PollerB/1.0"})

    rows = (await session.execute(select(FeedFetch).order_by(FeedFetch.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].client_hash != rows[1].client_hash


async def test_venue_filter_lands_verbatim_when_present(client, session, make_venue):
    await make_venue(slug="cats-cradle")

    await client.get("/feeds/events.ics", params={"venue": "cats-cradle"})

    row = (await session.execute(select(FeedFetch))).scalar_one()
    assert row.venue_filter == "cats-cradle"


async def test_venue_filter_is_null_when_absent(client, session):
    await client.get("/feeds/events.ics")

    row = (await session.execute(select(FeedFetch))).scalar_one()
    assert row.venue_filter is None


async def test_last_xff_entry_wins_over_earlier_entries_and_socket_peer(client, session):
    # First entry is caller-controlled (Railway's edge appends the real address, it
    # never rewrites earlier hops), so a spoofed leading IP must not affect the hash.
    # httpx's ASGITransport supplies a fixed request.client peer distinct from either
    # XFF entry, so a match here also proves XFF wins over the socket peer.
    await client.get("/feeds/events.ics", headers={"X-Forwarded-For": "9.9.9.9, 5.5.5.5"})
    await client.get("/feeds/events.ics", headers={"X-Forwarded-For": "5.5.5.5"})

    rows = (await session.execute(select(FeedFetch).order_by(FeedFetch.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].client_hash == rows[1].client_hash


async def test_production_with_empty_salt_records_nothing_but_still_serves_200(client, session, monkeypatch, caplog):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "TELEMETRY_SALT", "")

    resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200

    count = (await session.execute(select(func.count(FeedFetch.id)))).scalar()
    assert count == 0
    assert any("TELEMETRY_SALT" in record.message for record in caplog.records)


async def test_recorder_exception_is_swallowed_and_feed_still_returns_200(client, session, monkeypatch):
    import app.api.feeds as feeds_module

    def _boom(*args, **kwargs):
        raise RuntimeError("telemetry backend exploded")

    monkeypatch.setattr(feeds_module.hashlib, "sha256", _boom)

    resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200

    count = (await session.execute(select(func.count(FeedFetch.id)))).scalar()
    assert count == 0
