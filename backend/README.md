# Triangle Shows — backend

FastAPI service that scrapes Triangle-area venue listings into PostgreSQL and serves them as an API. See `app/main.py` for the application entry point and `../README.md` for the project overview.

## API surface

The versioned `/api/v1` endpoints (`/api/v1/events`, `/api/v1/events/{id}`, `/api/v1/venues`, `/api/v1/health`) are the canonical, client-agnostic contract. The unversioned `/api/events`, `/api/venues`, and `/api/health` routes are deprecated aliases kept for the current web client; `/feeds/events.ics` is the iCal subscription feed. Shared fetch/filter/de-duplication logic lives in `app/services/events_query.py` and shared route helpers in `app/api/common.py`, so every surface serves the same data.

## API contracts

Three deliberate contract choices, called out so they aren't mistaken for bugs:

- **Presentation is the client's job.** `/api/v1/events` returns neutral event resources — no `title`, `backgroundColor`, or `extendedProps`, and no formatted price or 12-hour time strings. The web client builds the FullCalendar shape from those resources in `frontend/js/fullcalendar-adapter.js`. The old server-shaped `GET /api/events/fullcalendar` feed was removed once that logic moved client-side; any non-web consumer (e.g. iOS via the WXYC Backend-Service) builds its own presentation the same way.
- **Calendar de-duplicates; the iCal feed does not.** The `/api/v1/events` and `/api/events` JSON surfaces cross-venue de-duplicate — when the same artist plays the same date at two venues, the record with the most complete metadata wins, so the calendar grid shows one tile per artist/date. `/feeds/events.ics` intentionally keeps every *live* listing (it queries with `dedup=False`; soft-removed events drop out of the feed like every list surface): a calendar *subscriber* should see each venue's offering rather than a collapsed view. This asymmetry is by design — don't "fix" one surface to match the other.
- **Delisted events are soft-tombstoned, not deleted.** When an event a venue previously advertised goes missing from that venue's scrape snapshots on two distinct calendar days (as little as ~12 hours apart under the scheduled cadence), the scrape diff stamps `removed_at` (see `app/scrapers/manager.py` — misses are guarded by a per-day cap, a per-scrape horizon, a mass-disappearance guard, and a streak-staleness window). Two deliberate blind spots follow from the guards: a venue that delists the *majority* of its in-window calendar at once produces no tombstones (indistinguishable from scraper breakage; those events age out via the past-date cleanup instead), and conversely a single event a scraper persistently fails to parse reads as a delisting until the parse recovers (reappearance self-heals). List surfaces exclude tombstoned events by default; `/api/v1/events?include_removed=true` opts in, and the detail endpoint always resolves a tombstoned id. Mirror-style consumers must combine `include_removed=true` with `dedup=false` **and an explicit back-dated `start`** (e.g. 8 days ago) — the default `start=today` window hides a tombstone stamped on the event's own show date. `removed_at` records "the venue no longer advertises this" — an observation with a day-of blind spot; `status` is never inferred from it, and the 7-day past-date cleanup remains the only thing that deletes rows.

## Event identity: per-scraper audit

Each scraper class declares a machine-readable verdict, `URL_IDENTITY` (see `app/scrapers/identity.py::UrlIdentityVerdict`), answering one question: **may this scraper's `source_url` serve as event identity?** `TRUSTED` asserts both rename/reschedule stability (the source keeps the URL when the event is edited) and occurrence-uniqueness (one URL never covers two event-dates). Anything less is `HASH_FALLBACK`: the scraper's events reconcile by `external_id` when present, else content hash, and `source_url` is never an identity key. The verdict gates URL-tier reconciliation, the `source_key` migration backfill, and the duplicate merge — it is consumed from code (`url_identity_verdict(scraper_type)`), and the table below is a human-readable summary of those declarations (the code is canonical).

| Scraper type | Verdict | Why |
|---|---|---|
| `ticketmaster` | HASH_FALLBACK | `source_url` is the ticket page (not guaranteed event-unique); identity comes from `external_id`, the Ticketmaster event id |
| `venuepilot` | HASH_FALLBACK | `source_url` is `ticketsUrl` (not guaranteed event-unique); identity comes from `external_id`, the VenuePilot event id |
| `mec` | TRUSTED | `source_url` is the event's own JSON-LD `url` (per-event detail page); slugs persist across renames |
| `tribe_events` | TRUSTED | per-event JSON-LD/detail URL; The Events Calendar emits occurrence-specific URLs for recurring events |
| `rhp_events` | TRUSTED | per-event detail-page link from the event wrapper |
| `motorco` | TRUSTED | per-event url from the calendar's JS event blocks (WordPress detail page) |
| `eventprime` | TRUSTED | per-event detail link from the listing row |
| `carolina_theatre` | TRUSTED | per-event card link to the event's detail page (venue currently removed in `seed.py`; re-audit if re-added) |
| `koka_booth` | TRUSTED | event's own JSON-LD `url` or `None` — never the shared listing page |
| `squarespace` | HASH_FALLBACK | `fullUrl` is regenerated from the title on rename — not rename-stable |
| `webflow_cms` | HASH_FALLBACK | `source_url` is the ticket link, not guaranteed event-unique |
| `tickpick_organizer` | HASH_FALLBACK | TickPick ticket page; event-uniqueness across an organizer's listings is unverified |

A new scraper must declare its own verdict — `tests/test_identity.py` fails if one is missing from the registry or relies on an inherited default. When in doubt, declare `HASH_FALLBACK`: it preserves today's content-hash behavior, while a wrong `TRUSTED` can merge distinct events into one row.

## The `source_key` contract

Every event carries a `source_key` — a stable, tier-prefixed identity string exposed on `GET /api/v1/events` and `GET /api/v1/events/{id}`. It is the key external consumers reconcile on (WXYC Backend-Service upserts concerts as `(source='triangle_shows', source_id=source_key)`); treat its derivation as a published contract and change it only with a documented migration plan.

**Derivation** (`app/scrapers/identity.py::derive_source_key`, precedence order):

1. `ext:<external_id>` — when the scraper supplies a source-system id (Ticketmaster, VenuePilot).
2. `url:<normalized source_url>` — only for scrapers whose audit verdict is TRUSTED. Normalization (`normalize_source_url`) strips scheme, host, fragment, and a trailing slash; keeps path + query (ticketing pages may carry identity in a query parameter) with query parameters sorted by name so param order can't change identity; and removes known tracking params (`utm_*`, `fbclid`, `gclid`).
3. `hash:<sha256>` — the content hash of `(venue_slug | date | normalized name)`, for everything else.

**Stability classes** — the prefix tells you what you can rely on:

- `ext:` and `url:` keys survive renames and reschedules: the row updates in place and the key does not change.
- `hash:` keys do NOT survive renames or reschedules — the name and date are baked into the hash, so consumers see a delete+create pair for those venues. This is inherent to hash-fallback venues (see the audit table above).
- A key can migrate tiers (e.g. a scraper starts supplying `external_id` for an event previously keyed by URL). The row is preserved — reconciliation matches on per-tier columns, not on `source_key` — but the key value changes, which a consumer sees as delete+create churn. Tier shifts are rare, one-time events per row.

**Uniqueness** is per-venue: `(venue_id, source_key)` is unique; `source_key` alone is not (VenuePilot ids are small integers that collide across venues).

**One-time churn window after the identity migration**: rows whose stored `source_url` was a shared listing-page URL (the pre-fix mec scraper) migrate to per-event keys over the first scrape cycle after deploy. Consumers should begin keying on `source_key` only after that cycle completes.

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
