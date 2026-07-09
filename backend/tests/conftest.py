"""
Pytest fixtures for the Triangle Shows backend.

Provides an ephemeral PostgreSQL-backed harness: a fresh schema per test
(``create_all``/``drop_all``), an ``httpx.AsyncClient`` bound to the FastAPI app
with the database dependency pointed at the test database, and ORM factories that
insert deterministic ``Venue``/``Event`` rows without running the scrapers.

Why PostgreSQL and not SQLite: production runs PostgreSQL, and testing against the
same engine keeps dialect-specific behavior (JSON columns, timestamp semantics,
future ``ON CONFLICT`` upserts) exercised rather than approximated. By default the
harness targets the docker-compose Postgres (see ``docker-compose.yml``) using a
dedicated ``*_test`` database it creates on first use. Override the whole URL with
``DATABASE_URL_TEST``. Under ``pytest-xdist`` each worker gets its own database so
parallel runs do not collide.
"""

import hashlib
import os
import re
from datetime import date, timedelta

from sqlalchemy.engine import make_url

# Default date for factory-made events: fixed at collection time, a month in the
# future so tests keep passing as the wall clock advances (the v1 API defaults its
# window to "today onward"). Test modules import this as their date anchor so the
# factory default and test filters can never drift apart.
DEFAULT_EVENT_DATE = date.today() + timedelta(days=30)

# --- Pin the app to the test database BEFORE importing any app.* module ---------
# app.config.settings and the engine in app.database read DATABASE_URL at import
# time, so the environment must be set first.


def _resolve_test_db_url() -> str:
    url = os.environ.get(
        "DATABASE_URL_TEST",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/triangle_shows_test",
    )
    parsed = make_url(url)
    # Give each pytest-xdist worker its own database so parallel runs don't collide.
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        parsed = parsed.set(database=f"{parsed.database}_{worker}")
    # Safety rail: never run the destructive create_all/drop_all cycle against a
    # database whose name doesn't clearly mark it as a test database. A raise (not
    # assert, which -O strips) and a word-boundary match (which "latest"/"contest"
    # don't satisfy) keep the rail honest.
    if not (parsed.database and re.search(r"(^|_)test(_|$)", parsed.database.lower())):
        raise ValueError(
            f"Refusing to use non-test database {parsed.database!r}; point DATABASE_URL_TEST "
            "at a database whose name contains a 'test' component (e.g. triangle_shows_test)."
        )
    return parsed.render_as_string(hide_password=False)


TEST_DATABASE_URL = _resolve_test_db_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("RUN_STARTUP_SCRAPE", "false")
os.environ.setdefault("ENABLE_SCHEDULER", "false")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

# Importing app.main builds the FastAPI instance and (transitively, via the routers)
# registers every ORM model on Base.metadata. The explicit models import is a guard
# so schema creation doesn't silently depend on which modules the routers happen to
# import.
from app.database import Base, get_session  # noqa: E402
from app.models import Event, ScrapeLog, Venue  # noqa: E402,F401
from app.main import app  # noqa: E402


def _create_test_database_if_missing() -> None:
    """Create the target test database if it doesn't exist (sync, one-shot).

    Uses psycopg2 against the maintenance ``postgres`` database, connecting via a
    libpq URI derived from the full test URL so connection details beyond
    host/port/user (e.g. ``?host=/socket/path``, ``sslmode``) carry through.
    ``CREATE DATABASE`` can't run inside a transaction, so the connection is put in
    autocommit mode.
    """
    import psycopg2
    from psycopg2 import sql

    url = make_url(TEST_DATABASE_URL)
    maintenance_uri = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    conn = psycopg2.connect(maintenance_uri)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (url.database,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))
    finally:
        conn.close()


@pytest.fixture(scope="session")
def _ensure_test_database():
    """Create the per-run (per-worker) test database once before any test connects.

    Not autouse: every database touch flows through ``_engine``, whose explicit
    dependency on this fixture provides the create-before-connect ordering.
    """
    _create_test_database_if_missing()


@pytest_asyncio.fixture
async def _engine(_ensure_test_database):
    """Function-scoped engine with a fresh schema; dropped and disposed after the test.

    Fresh-schema-per-test is chosen over transaction rollback because it needs no
    nested-transaction plumbing through the get_session dependency and the schema is
    tiny. If the suite ever grows past ~500 tests or per-test setup exceeds ~100ms,
    revisit and switch to a connection-scoped savepoint fixture (see backend/README.md).
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        # Nested try/finally: dispose must run even when drop_all fails, or every
        # subsequent test leaks a connection pool.
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            await engine.dispose()


@pytest.fixture
def _sessionmaker(_engine):
    return async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(_sessionmaker):
    """A database session for inserting fixtures directly via the ORM."""
    async with _sessionmaker() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def client(_sessionmaker):
    """An httpx AsyncClient bound to the app, with get_session pointed at the test DB.

    httpx's ASGITransport does not drive the app's lifespan, so migrations, venue
    seeding, the startup scrape, and the scheduler do not run here — the schema comes
    from create_all and data comes from the ORM factories.
    """

    async def _override_get_session():
        async with _sessionmaker() as db_session:
            yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def make_venue(session):
    """Factory that inserts and commits a Venue, returning the persisted row.

    Sensible defaults are filled in; pass overrides for any field a test cares about.
    """
    created: list[Venue] = []

    async def _make(**overrides) -> Venue:
        n = len(created) + 1
        fields = dict(
            name=f"Test Venue {n}",
            slug=f"test-venue-{n}",
            city="Durham",
            size_category="medium",
            scraper_type="manual",
            color="#c87941",
        )
        fields.update(overrides)
        venue = Venue(**fields)
        session.add(venue)
        await session.commit()
        created.append(venue)
        return venue

    return _make


@pytest_asyncio.fixture
async def make_event(session, make_venue):
    """Factory that inserts and commits an Event (creating a Venue if none is given).

    ``hash`` is required and unique on the model; a stable one is derived when not
    supplied so tests don't have to think about it. The default date is
    DEFAULT_EVENT_DATE (a month in the future) so tests keep passing as the wall
    clock advances.
    """
    created: list[Event] = []

    async def _make(venue=None, **overrides) -> Event:
        if venue is None:
            venue = await make_venue()
        n = len(created) + 1
        fields = dict(
            venue_id=venue.id,
            name=overrides.get("name") or overrides.get("artist") or f"Test Show {n}",
            date=DEFAULT_EVENT_DATE,
            source="manual",
        )
        fields.update(overrides)
        if not fields.get("hash"):
            raw = f"{fields['venue_id']}|{fields['name']}|{fields['date']}|{n}"
            fields["hash"] = hashlib.sha256(raw.encode()).hexdigest()
        event = Event(**fields)
        session.add(event)
        await session.commit()
        created.append(event)
        return event

    return _make
