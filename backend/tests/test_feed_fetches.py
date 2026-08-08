"""Tests for server-side .ics feed telemetry (issue #88).

Every successfully served GET /feeds/events.ics records one append-only row in
feed_fetches, keyed by a salted, truncated hash of (client IP, user agent) —
never the raw values. The write is best-effort: telemetry failures must never
break the feed response. See app/api/feeds.py::record_feed_fetch.

Several assertions here exist because mutation testing showed the obvious version
was vacuous: asserting two hashes are *equal* passes just as well when the input
those hashes are supposed to depend on is ignored entirely. So the salt, the
X-Forwarded-For resolution, and the rollback each get a test that goes red when
the behavior is removed, not just one that goes green when it is present.
"""

import logging

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.feeds as feeds
from app.config import settings
from app.database import get_session
from app.main import app
from app.models import FeedFetch


@pytest.fixture(autouse=True)
def _reset_missing_salt_warning():
    """Clear the once-per-process missing-salt warning around every test.

    The warning is deliberately emitted once per process (see feeds.py), so without
    this the tests that assert on it would pass or fail depending on which test ran
    first.
    """
    feeds._warn_telemetry_salt_missing.cache_clear()
    yield
    feeds._warn_telemetry_salt_missing.cache_clear()


async def _failing_commit(self, *args, **kwargs):
    """Stand-in for AsyncSession.commit that fails the way a real write failure does.

    ``execute`` autoflushes the pending INSERT first, then the division blows up
    inside the same transaction — leaving it aborted, which is precisely the state
    the recorder's ``rollback`` exists to clear. A bare ``raise`` would leave the
    transaction clean and never exercise that branch.
    """
    await self.execute(text("SELECT 1 / 0"))


# --- The write itself ---

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


# --- What the hash is a function of ---

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


async def test_client_hash_differs_for_a_different_forwarded_ip(client, session):
    headers = {"User-Agent": "TestPoller/1.0"}
    await client.get("/feeds/events.ics", headers={**headers, "X-Forwarded-For": "5.5.5.5"})
    await client.get("/feeds/events.ics", headers={**headers, "X-Forwarded-For": "6.6.6.6"})

    rows = (await session.execute(select(FeedFetch).order_by(FeedFetch.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].client_hash != rows[1].client_hash


async def test_client_hash_depends_on_the_telemetry_salt(client, session, monkeypatch):
    """Two deployments with different salts must not produce comparable hashes.

    Goes red if the salt is dropped from the digest input — the whole point of a
    per-deployment salt is that a stolen client_hash can't be brute-forced back to
    a source IP with a precomputed table.
    """
    headers = {"User-Agent": "TestPoller/1.0", "X-Forwarded-For": "5.5.5.5"}

    monkeypatch.setattr(settings, "TELEMETRY_SALT", "salt-alpha")
    await client.get("/feeds/events.ics", headers=headers)
    monkeypatch.setattr(settings, "TELEMETRY_SALT", "salt-beta")
    await client.get("/feeds/events.ics", headers=headers)

    rows = (await session.execute(select(FeedFetch).order_by(FeedFetch.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].client_hash != rows[1].client_hash


# --- Client IP resolution ---

async def test_last_xff_entry_wins_over_earlier_entries(client, session):
    # The first entry is caller-controlled (Railway's edge appends the real address,
    # it never rewrites earlier hops), so a spoofed leading IP must not move the hash.
    await client.get("/feeds/events.ics", headers={"X-Forwarded-For": "9.9.9.9, 5.5.5.5"})
    await client.get("/feeds/events.ics", headers={"X-Forwarded-For": "5.5.5.5"})

    rows = (await session.execute(select(FeedFetch).order_by(FeedFetch.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].client_hash == rows[1].client_hash


async def test_forwarded_ip_wins_over_the_socket_peer(client, session):
    """A forwarded address must actually be *used*, not merely tolerated.

    Equality-only XFF assertions hold just as well when the header is ignored and
    every request falls through to the socket peer, so this asserts divergence:
    httpx's ASGITransport supplies a fixed peer (127.0.0.1) for both requests, and
    only the forwarded address can tell them apart.
    """
    headers = {"User-Agent": "TestPoller/1.0"}
    await client.get("/feeds/events.ics", headers={**headers, "X-Forwarded-For": "5.5.5.5"})
    await client.get("/feeds/events.ics", headers=headers)

    rows = (await session.execute(select(FeedFetch).order_by(FeedFetch.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].client_hash != rows[1].client_hash


# --- venue_filter records what was actually served ---

async def test_venue_filter_records_a_single_slug(client, session, make_venue):
    await make_venue(slug="cats-cradle")

    await client.get("/feeds/events.ics", params={"venue": "cats-cradle"})

    row = (await session.execute(select(FeedFetch))).scalar_one()
    assert row.venue_filter == "cats-cradle"


@pytest.mark.parametrize(
    "venue_param",
    ["cats-cradle,local-506", "local-506,cats-cradle", "cats-cradle, local-506"],
)
async def test_venue_filter_is_normalized_so_one_cohort_stays_one_bucket(
    client, session, venue_param
):
    """Three spellings of the same filter serve identical feeds, so they must record
    identical values — otherwise GROUP BY venue_filter splits one cohort three ways.
    The UI builds this parameter in click order (frontend/js/filters.js), so reordered
    spellings are the normal case, not an edge case."""
    await client.get("/feeds/events.ics", params={"venue": venue_param})

    row = (await session.execute(select(FeedFetch))).scalar_one()
    assert row.venue_filter == "cats-cradle,local-506"


async def test_venue_filter_is_null_when_absent(client, session):
    await client.get("/feeds/events.ics")

    row = (await session.execute(select(FeedFetch))).scalar_one()
    assert row.venue_filter is None


async def test_venue_filter_is_null_when_the_param_is_present_but_empty(client, session):
    """``?venue=`` serves the full calendar, byte-identical to the no-param response,
    so it belongs in the same NULL bucket — recording '' would file a full-calendar
    subscriber as a per-venue subscriber of a venue named ''."""
    resp = await client.get("/feeds/events.ics", params={"venue": ""})
    assert resp.status_code == 200

    row = (await session.execute(select(FeedFetch))).scalar_one()
    assert row.venue_filter is None


# --- The salt is required, unconditionally ---

async def test_an_empty_salt_records_nothing_but_still_serves_200(client, session, monkeypatch, caplog):
    """No salt, no rows — on every deployment, not just ones that set APP_ENV.

    The recorder must not depend on an environment variable to decide whether the
    privacy control applies: this fork deploys via `railway redeploy --from-source`,
    which sets nothing, so an APP_ENV-gated guard would never fire in production.
    """
    monkeypatch.setattr(settings, "TELEMETRY_SALT", "")

    with caplog.at_level(logging.WARNING):
        resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200

    count = (await session.execute(select(func.count(FeedFetch.id)))).scalar()
    assert count == 0
    assert any("TELEMETRY_SALT" in record.message for record in caplog.records)


@pytest.mark.parametrize("app_env", ["development", "production"])
async def test_the_salt_requirement_is_independent_of_app_env(client, session, monkeypatch, app_env):
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    monkeypatch.setattr(settings, "TELEMETRY_SALT", "")

    resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200

    count = (await session.execute(select(func.count(FeedFetch.id)))).scalar()
    assert count == 0


async def test_the_missing_salt_warning_is_logged_once_per_process(client, monkeypatch, caplog):
    """/feeds/events.ics is the highest-frequency endpoint in the app — calendar
    clients poll it unattended — so a per-request warning about a condition that is
    static for the process's lifetime would be one log line per poll, forever."""
    monkeypatch.setattr(settings, "TELEMETRY_SALT", "")

    with caplog.at_level(logging.WARNING):
        await client.get("/feeds/events.ics")
        await client.get("/feeds/events.ics")
        await client.get("/feeds/events.ics")

    salt_warnings = [r for r in caplog.records if "TELEMETRY_SALT" in r.message]
    assert len(salt_warnings) == 1


# --- Failure is contained ---

async def test_a_failed_write_leaves_the_feed_and_the_session_intact(client, session, monkeypatch):
    """The recorder must swallow the failure *and* undo the aborted transaction.

    Pointing the fault at ``commit`` (rather than at something upstream of the
    session, which never stages anything) is what makes this real: without the
    ``rollback``, the session is left holding a transaction PostgreSQL has already
    aborted, and the very next statement on it fails. The dependency is overridden
    to hand the endpoint this test's own session so that aftermath is observable —
    a per-request session would be closed before we could look at it.
    """

    async def _use_test_session():
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    monkeypatch.setattr(AsyncSession, "commit", _failing_commit)

    resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200

    monkeypatch.undo()
    count = (await session.execute(select(func.count(FeedFetch.id)))).scalar()
    assert count == 0
