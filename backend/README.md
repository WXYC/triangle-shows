# Triangle Shows — backend

FastAPI service that scrapes Triangle-area venue listings into PostgreSQL and serves them as an API. See `app/main.py` for the application entry point and `../README.md` for the project overview.

## API surface

The versioned `/api/v1` endpoints (`/api/v1/events`, `/api/v1/events/{id}`, `/api/v1/venues`, `/api/v1/health`) are the canonical, client-agnostic contract. The unversioned `/api/events`, `/api/venues`, and `/api/health` routes are deprecated aliases kept for the current web client; `/feeds/events.ics` is the iCal subscription feed. Shared fetch/filter/de-duplication logic lives in `app/services/events_query.py` and shared route helpers in `app/api/common.py`, so every surface serves the same data.

## API contracts

Two deliberate contract choices, called out so they aren't mistaken for bugs:

- **Presentation is the client's job.** `/api/v1/events` returns neutral event resources — no `title`, `backgroundColor`, or `extendedProps`, and no formatted price or 12-hour time strings. The web client builds the FullCalendar shape from those resources in `frontend/js/fullcalendar-adapter.js`. The old server-shaped `GET /api/events/fullcalendar` feed was removed once that logic moved client-side; any non-web consumer (e.g. iOS via the WXYC Backend-Service) builds its own presentation the same way.
- **Calendar de-duplicates; the iCal feed does not.** The `/api/v1/events` and `/api/events` JSON surfaces cross-venue de-duplicate — when the same artist plays the same date at two venues, the record with the most complete metadata wins, so the calendar grid shows one tile per artist/date. `/feeds/events.ics` intentionally keeps every listing (it queries with `dedup=False`): a calendar *subscriber* should see each venue's offering rather than a collapsed view. This asymmetry is by design — don't "fix" one surface to match the other.

## Running locally

```bash
# From the repo root — starts PostgreSQL and the API (see ../docker-compose.yml):
docker compose up
```

The API comes up on http://localhost:8000, with the auto-generated OpenAPI docs at http://localhost:8000/docs.

## Tests

The suite runs against **real PostgreSQL** — the same engine as production — so dialect-specific behavior (JSON columns, timestamp semantics, future `ON CONFLICT` upserts) is exercised rather than approximated by SQLite.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or: uv venv --python 3.12 .venv
pip install -r requirements-dev.txt

# Point at a PostgreSQL for tests. The default expects the docker-compose db on :5432:
docker compose up -d db          # from the repo root
pytest
pytest -n auto                   # parallel (pytest-xdist); each worker gets its own database
```

To use a different/isolated database, set `DATABASE_URL_TEST`. The database name must contain a `test` component set off by underscores or the ends of the name (e.g. `triangle_shows_test`, `test_db`) — the harness refuses anything else before running its destructive schema cycle:

```bash
docker run -d --rm --name ts-test-pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16-alpine
DATABASE_URL_TEST=postgresql+asyncpg://postgres:postgres@localhost:55432/triangle_shows_test pytest
```

### How the harness works

The details (and the rationale for each choice) live in the docstrings of `tests/conftest.py`; the short version:

- The test database is **created automatically** on first use; under `pytest-xdist` each worker gets its own (`triangle_shows_test_gw0`, …).
- **Isolation is fresh-schema-per-test** (`create_all`/`drop_all`) — revisit if the suite grows past ~500 tests.
- The `client` fixture uses `httpx.ASGITransport`, which does **not** run the app lifespan, so migrations, venue seeding, the startup scrape, and the scheduler stay off during tests. Use the `make_venue` / `make_event` fixtures to insert deterministic rows via the ORM.
