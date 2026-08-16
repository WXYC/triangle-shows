"""
FastAPI application entry point — initializes the database, applies migrations,
seeds venues, optionally starts a background scheduler, and mounts all routes.

Role: First code executed at server startup. The lifespan context manager runs
before any requests are served; Cloud Scheduler later hits POST /api/scrape to
trigger periodic re-scrapes every 6 hours.

Requires: DATABASE_URL, LOG_LEVEL, ENABLE_SCHEDULER, RUN_STARTUP_SCRAPE env vars
(via app.config); asyncpg-compatible PostgreSQL; Alembic migrations in backend/alembic/.
"""
# --- Imports ---
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import async_session
from app.observability import flush_errors, init_error_tracking, report_error
from app.redaction import RedactingFormatter, redact_credentials, redact_handler
from app.seed import seed_venues
from app.scheduler import scheduler, configure_scheduler
from app.site_config import load_site_config
from app.api import events, venues, health, feeds, v1

# --- Logging setup ---

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging() -> None:
    """Configure process-wide logging, with credential redaction on every sink.

    Two measures, because the Ticketmaster Discovery API authenticates with a query
    parameter and so every request URL the scraper builds is a live credential:

    * ``httpx`` is pinned to WARNING. It logs the full URL of every request at INFO,
      which put the key in the log store four times per scrape cycle. Our scrapers
      already emit their own per-request line naming the venue, so what is lost is the
      HTTP status of a *successful* request; a failure still raises and is logged.
    * Every root handler gets a :class:`~app.redaction.RedactingFormatter`, which scrubs
      the values out of anything else that renders a URL — including the exception
      tracebacks the httpx pin cannot reach, since ``httpx.HTTPStatusError`` carries the
      request URL in its own message regardless of the logger's level.
    * uvicorn's *own* handlers get the same treatment via
      :func:`~app.redaction.redact_handler`. Root handlers alone are not enough: the
      ``uvicorn`` logger is configured with ``propagate=False`` and its own handler, so
      nothing it logs ever reaches a root handler. That matters because Starlette's
      ``ServerErrorMiddleware`` always re-raises after invoking the bare-``Exception``
      handler, and uvicorn then logs the full traceback itself on ``uvicorn.error`` — so
      an ``httpx.HTTPStatusError`` escaping any route would otherwise write a live
      ``?apikey=`` to the log stream unredacted. ``redact_handler`` *wraps* uvicorn's
      formatters rather than replacing them, so its log lines keep their existing shape.

    Called at import so configuration is in place before any other module logs, and
    exposed as a function so tests can assert on it without importing for its side
    effects alone. uvicorn builds its logging config in ``Config.__init__``, before it
    imports the app, so its handlers already exist by the time this runs under a real
    server; the loop simply finds nothing under pytest or a bare interpreter.
    """
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL), format=LOG_FORMAT)

    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter(LOG_FORMAT))

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(logger_name).handlers:
            redact_handler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)


configure_logging()
logger = logging.getLogger(__name__)


# --- Startup helpers ---

def _run_migrations():
    """Run alembic upgrade head synchronously (called via asyncio.to_thread)."""
    from alembic import command
    from alembic.config import Config

    # alembic.ini lives one directory above this file (i.e. /app/alembic.ini in Docker)
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    # Migrations run in-process, so alembic.ini's logging config would reconfigure the
    # whole server's logging — disabling every app.* logger and dropping root to WARN.
    # alembic/env.py honors this attribute; the shell `alembic upgrade head` path,
    # where that config is exactly what you want, leaves it unset.
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


async def _startup_scrape():
    """Run a full scrape in the background on startup."""
    logger.info("Startup scrape: beginning...")
    try:
        from app.scrapers.manager import ScrapeManager
        async with async_session() as session:
            manager = ScrapeManager(session)
            results = await manager.scrape_all()
            for r in results:
                logger.info(f"  [startup] {r}")
        logger.info("Startup scrape: complete")
    except Exception as e:
        # Non-fatal: the API should still serve cached data even if the scrape fails
        report_error(e, where="main._startup_scrape")


# --- Lifespan (startup / shutdown) ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info(f"Starting {_site.name} API...")

    try:
        # Apply any pending Alembic migrations — creates tables on fresh DBs, updates schema on existing ones
        await asyncio.to_thread(_run_migrations)
        logger.info("Migrations applied")

        # Seed venues
        await seed_venues()
        logger.info("Venues seeded")

        # Kick off a scrape immediately in the background (skipped when RUN_STARTUP_SCRAPE
        # is false, e.g. under tests or when seeding data manually). The Task is kept on
        # app.state because the event loop holds only a weak reference — an unreferenced
        # task can be garbage-collected mid-scrape.
        if settings.RUN_STARTUP_SCRAPE:
            app.state.startup_scrape_task = asyncio.create_task(_startup_scrape())
            logger.info("Startup scrape scheduled")
        else:
            logger.info("Startup scrape disabled (RUN_STARTUP_SCRAPE=false)")

        # Start scheduler if enabled
        if settings.ENABLE_SCHEDULER:
            configure_scheduler()
            scheduler.start()
            logger.info("Scheduler started")
    except Exception as e:
        # Migrations and seed_venues are unguarded above on purpose: a failure here
        # is fatal and must crash-loop the container, not limp along on an empty or
        # stale database. What was missing was the signal — this makes the crash
        # loud instead of silent. flush_errors() matters because re-raising exits
        # uvicorn immediately after; a buffered tracker event would never ship
        # otherwise.
        report_error(e, where="main.lifespan.startup")
        flush_errors()
        raise

    yield

    # Shutdown
    scrape_task = getattr(app.state, "startup_scrape_task", None)
    if scrape_task is not None and not scrape_task.done():
        # Cancel and await the in-flight scrape so shutdown doesn't abandon a pending
        # task mid-transaction ("Task was destroyed but it is pending!").
        scrape_task.cancel()
        with suppress(asyncio.CancelledError):
            await scrape_task
        logger.info("Startup scrape cancelled at shutdown")
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down")


# --- App instantiation ---

# Loaded at import time (not lifespan): the FastAPI title/description are static
# attributes set once at construction, and httpx's ASGITransport (the test
# `client` fixture) never drives the app lifespan anyway (tests/conftest.py) — so
# this doubles as the "REGION=triangle boot fails loudly on a bad/missing pack"
# fail-fast (region-pack epic decision 5): importing app.main dies immediately on
# a malformed or missing site.toml, same as any deploy that imports this module.
_site = load_site_config().site

# Before app construction, not in the lifespan: Starlette builds the middleware
# stack lazily inside Starlette.__call__ (the lifespan scope is itself a pass
# through __call__), so by the time the lifespan body ran the stack would already
# exist and Sentry's FastAPI/Starlette integration patching would land too late to
# enrich it. This also runs before `app` exists, which is when the equivalent
# class-level route-handler patching wants to happen. A no-op with no SENTRY_DSN
# set (the test environment), so sentry_sdk.init never runs under pytest.
init_error_tracking()

app = FastAPI(
    title=f"{_site.name} API",
    description=(
        f"Surface-neutral API for {_site.name}'s live-music events and venues. "
        "The versioned /api/v1 endpoints are the canonical, client-agnostic contract "
        "(consumed by the web calendar and other clients); the unversioned /api/events, "
        "/api/venues, and /api/health endpoints are deprecated aliases."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Route registration ---

# API routes — v1 is the canonical surface; the unversioned routers are deprecated aliases.
app.include_router(v1.router)
app.include_router(events.router)
app.include_router(venues.router)
app.include_router(health.router)
app.include_router(feeds.router)

# Manual scrape trigger (dev only)
@app.post("/api/scrape")
async def trigger_scrape(scraper_type: str = None):
    """Manually trigger a scrape (development use)."""
    from app.database import async_session
    from app.scrapers.manager import ScrapeManager
    from fastapi import HTTPException

    try:
        async with async_session() as session:
            manager = ScrapeManager(session)
            if scraper_type:
                results = await manager.scrape_all(scraper_types=[scraper_type])
            else:
                results = await manager.scrape_all()
            return {"results": results}
    except Exception as e:
        report_error(e, where="main.trigger_scrape")
        # This endpoint is unauthenticated, and failures here happen *outside*
        # scrape_venue (session construction, a scraper import) — before manager's
        # own redaction runs — so this catch-all must scrub independently rather
        # than rely on the per-venue result dict's redaction.
        raise HTTPException(status_code=500, detail=redact_credentials(str(e)))


async def _unhandled_exception_handler(request, exc: Exception):
    """Catch every otherwise-unhandled request exception so it is captured instead
    of just becoming a 500 and a uvicorn log line. Registered for bare Exception,
    leaving Starlette's own HTTPException handling untouched.

    The response body is a fixed, opaque message — never str(exc). This handler is
    a response sink on *every* route; echoing the exception would turn the
    credential leak trigger_scrape used to have (see above) into an every-route one.

    report_error is guarded because Starlette invokes this handler *before* sending
    the response (ServerErrorMiddleware: handler first, then `if not response_started`).
    A raising funnel — a wedged tracker transport, a formatter blowing up on a weird
    payload — would therefore cost the client its 500 entirely and replace the original
    exception in the log with the reporting one. Capturing errors must never be the
    reason a response is lost.
    """
    try:
        report_error(exc, where="main.unhandled_exception", context={"path": str(request.url.path)})
    except Exception:
        logger.exception("[main.unhandled_exception] error capture itself failed")
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


app.add_exception_handler(Exception, _unhandled_exception_handler)


# --- Static file serving ---

# Serve frontend static files
# Check multiple possible locations (local dev vs Docker)
frontend_candidates = [
    Path(__file__).parent.parent.parent / "frontend",  # local dev
    Path("/frontend"),  # Docker
]
for frontend_dir in frontend_candidates:
    if frontend_dir.exists():
        # Mounted last so API routes take priority over the catch-all html=True handler
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        break
