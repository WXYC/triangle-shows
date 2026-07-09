# Triangle Shows — backend

FastAPI service that scrapes Triangle-area venue listings into PostgreSQL and serves them as an API. See `app/main.py` for the application entry point and `../README.md` for the project overview.

## Running locally

```bash
# From the repo root — starts PostgreSQL and the API (see ../docker-compose.yml):
docker compose up
```

The API comes up on http://localhost:8000, with the auto-generated OpenAPI docs at http://localhost:8000/docs.

## Tests

The suite runs against **real PostgreSQL** — SQLite is not a usable substitute because the scrape manager upserts via `postgresql.insert(...).on_conflict_*` and the models use `JSON` columns.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or: uv venv --python 3.12 .venv
pip install -r requirements-dev.txt

# Point at a PostgreSQL for tests. The default expects the docker-compose db on :5432:
docker compose up -d db          # from the repo root
pytest
```

To use a different/isolated database, set `DATABASE_URL_TEST` (any name containing `test`):

```bash
docker run -d --rm --name ts-test-pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16-alpine
DATABASE_URL_TEST=postgresql+asyncpg://postgres:postgres@localhost:55432/triangle_shows_test pytest
```

### How the harness works

- The test database (default `triangle_shows_test`) is **created automatically** on first use; you don't need to pre-create it.
- **Isolation is fresh-schema-per-test**: each test runs `create_all` then `drop_all`. This is chosen over transaction-rollback because it needs no nested-transaction plumbing through the `get_session` dependency and the schema is tiny. If the suite ever grows past ~500 tests or per-test setup exceeds ~100 ms, switch to a connection-scoped savepoint fixture.
- **Parallelism**: under `pytest-xdist` each worker gets its own database (`triangle_shows_test_gw0`, …) automatically, created on demand — no manual setup.
- The `client` fixture builds the app over `httpx.ASGITransport`, which does **not** run the app lifespan, so migrations, venue seeding, the startup scrape, and the scheduler stay off during tests. Use the `make_venue` / `make_event` fixtures to insert deterministic rows via the ORM.
