"""Data-contract tests for the venue seed list.

`venues.city` feeds downstream consumers verbatim (the deprecated and v1 serializers,
the web client's subdomain lock, and the Backend-Service ETL), so every seeded value
must be a real municipality — display groupings like "Chapel Hill-Carrboro" live in
the query/UI layers, never in the column.

VENUES itself is now sourced from the active region's venues.toml (app.site_config),
not a Python literal — see app/seed.py and backend/config/regions/triangle/venues.toml
(region-pack epic, issue #62/#63).
"""

import contextlib
import logging

from sqlalchemy import func, select

from app.models import Venue
from app.seed import VENUES, seed_venues

# The set of municipalities Triangle Shows actually covers. Extending coverage to a
# new town means adding it here deliberately — a typo or grouping label fails fast.
REAL_MUNICIPALITIES = {"Raleigh", "Cary", "Durham", "Chapel Hill", "Carrboro", "Saxapahaw"}


def test_every_seeded_city_is_a_real_municipality():
    for venue in VENUES:
        assert venue["city"] in REAL_MUNICIPALITIES, (
            f"{venue['name']}: {venue['city']!r} is not a real municipality"
        )


def test_venues_straddling_the_chapel_hill_carrboro_line_carry_their_actual_town():
    cities = {v["slug"]: v["city"] for v in VENUES}
    assert cities["the-cave"] == "Chapel Hill"
    assert cities["local-506"] == "Chapel Hill"
    assert cities["cats-cradle"] == "Carrboro"
    assert cities["cats-cradle-back-room"] == "Carrboro"
    # Koka Booth is in Cary, not Raleigh.
    assert cities["koka-booth"] == "Cary"


# --- Characterization pins (region-pack epic, issue #62/#63) -----------------------
#
# These pin today's exact venue count and seed idempotency so Phase 1's rewrite of
# seed.py (Python literal -> venues.toml) can't silently change behavior.
#
# seed_venues() normally runs against app.database's module-global engine, which is
# bound to whichever event loop was active when app.database was first imported —
# fine at real startup, but wrong across pytest-asyncio's per-test event loops. So
# these tests point app.seed at the `session` fixture's per-test engine/sessionmaker
# instead (same pattern test_vanished_events.py uses for the scheduler's session)
# and skip the redundant init_db() — the `session`/`_engine` fixture already created
# the schema.


async def test_seed_venues_loads_exactly_22_venues(session, monkeypatch):
    assert len(VENUES) == 22
    monkeypatch.setattr("app.seed.init_db", _noop_init_db)
    monkeypatch.setattr("app.seed.async_session", _session_stub(session))

    await seed_venues()

    count = await session.scalar(select(func.count()).select_from(Venue))
    assert count == 22

    cities = await session.scalars(select(Venue.city).distinct())
    assert set(cities.all()) == REAL_MUNICIPALITIES


async def test_seed_venues_is_idempotent(session, monkeypatch, caplog):
    monkeypatch.setattr("app.seed.init_db", _noop_init_db)
    monkeypatch.setattr("app.seed.async_session", _session_stub(session))

    with caplog.at_level(logging.INFO, logger="app.seed"):
        await seed_venues()
        first_count = await session.scalar(select(func.count()).select_from(Venue))

        caplog.clear()
        await seed_venues()
        second_count = await session.scalar(select(func.count()).select_from(Venue))

    assert first_count == 22
    assert second_count == 22
    assert "Seed complete: 0 new, 22 updated venues" in caplog.text


async def _noop_init_db():
    """seed_venues() calls init_db() first; the `session` fixture's engine already
    created the schema, so this stand-in skips a second, loop-mismatched create_all
    against app.database's module-global engine."""


def _session_stub(existing_session):
    """A zero-arg callable standing in for app.database.async_session, whose
    `async with async_session() as session:` normally opens a fresh session against
    the module-global engine — swapped out here for the test's per-test session."""

    @contextlib.asynccontextmanager
    async def _cm():
        yield existing_session

    return _cm
