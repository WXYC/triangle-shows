# triangle-shows.net

A concert calendar for venues across the Raleigh-Durham-Chapel Hill area.

**[triangle-shows.net](https://triangle-shows.net)**

---

## What it does

Triangle Shows aggregates live music listings from 21+ venues across the Triangle and displays them on an interactive calendar. Every 6 hours, a background scraper pulls fresh event data from each venue. Visitors can search events, filter by city or venue, favorite shows, hide clutter, and export to their personal calendar via `.ics` download or live subscription feed.

### Venues covered

| Venue | City |
|---|---|
| Boom Club | Durham |
| Carolina Theatre | Durham |
| Duke Arts | Durham |
| DPAC | Durham |
| Motorco Music Hall | Durham |
| Rubies on Five Points | Durham |
| Shadowbox Studio | Durham |
| Sharp 9 Gallery | Durham |
| Stancyks | Durham |
| The Pinhook | Durham |
| Cat's Cradle and Back Room | Carrboro |
| Local 506 | Chapel Hill |
| The Cave | Chapel Hill |
| Haw River Ballroom | Saxapahaw |
| Chapel of Bones | Raleigh |
| Kings | Raleigh |
| Koka Booth Amphitheatre | Cary |
| Lincoln Theatre | Raleigh |
| Neptune's Parlour | Raleigh |
| Pour House | Raleigh |
| Red Hat Amphitheater | Raleigh |
| Slim's | Raleigh |
| The Ritz | Raleigh |

---

## Features

- **Full-month calendar** — powered by FullCalendar v6 with month and list views
- **Search & filters** — filter by city, venue, or size; text search by artist/event name
- **Event details** — modal with doors/show time, price, description, and ticket link
- **Favorites** — heart events and export them as a `.ics` file
- **Hide shows** — hide events cluttering your view; restore them any time
- **Show proposals** — trusted contributors can propose a show (at a listed venue or a new one) at `/new-shows-form`, behind Cloudflare Access; an admin reviews, corrects and approves it on `/admin` before it reaches the calendar
- **Calendar subscription** — add `https://triangle-shows.net/feeds/events.ics` to Apple Calendar, Google Calendar, or Outlook for live updates
- **Color palettes** — 5 themes (Amber, Phosphor, Midnight, Wisteria, Durham) with light/dark modes
- **Durham site** — [durm-shows.net](https://durm-shows.net) shows only Durham venues with the Durham Bulls palette. `durm.triangle-shows.net` still works and serves the same thing

---

## Running locally

See [SELF-HOSTING.md](docs/SELF-HOSTING.md) for setup instructions.

---

## Running the tests

Two suites, and CI runs both on every pull request.

**Backend** — pytest, from `backend/`:

```bash
python -m pytest tests -q
```

A handful of migration tests need a reachable PostgreSQL server and skip cleanly without
one, so a local run with no database still passes. CI stands up a Postgres service
container, so they do run there.

**Frontend** — Node's built-in test runner, from the repo root:

```bash
node --test frontend/tests/
```

No `package.json`, no `npm install`, no dependencies: these use only `node:test`,
`node:assert`, `node:fs`, `node:path` and `node:vm`. Requires Node 18 or newer. Keeping
them dependency-free is deliberate — a browser-less runner that needs no toolchain is
what makes it reasonable to test plain `<script>` files at all, by evaluating them in a
`vm` context with only the globals they actually touch.

---

## Project structure

```
backend/
  app/
    api/            # FastAPI route handlers (v1 canonical surface, deprecated unversioned aliases, iCal feed, shared helpers)
    services/       # Shared query + cross-venue de-duplication logic used by every read surface
    scrapers/       # One scraper per venue/platform
    models.py       # SQLAlchemy ORM — Venue, Event, EventMissState, ScrapeLog, FeedFetch
    schemas.py      # Pydantic response models
    scheduler.py    # APScheduler job config
    seed.py         # Venue seed data (names, URLs, colors, capacities)
    config.py       # Settings loaded from .env
  alembic/          # Database migrations
  tests/            # pytest suite (real-PostgreSQL harness; see backend/README.md)
frontend/
  index.html
  css/styles.css
  js/
    app.js          # FullCalendar init, loading screen, hidden-show chips
    fullcalendar-adapter.js  # Maps neutral /api/v1 events → FullCalendar's event shape
    filters.js      # Search, city, venue, and size filter logic
    config.js       # Color palettes, API base URL, site config
    modal.js        # Event detail modal
    favorites.js    # Heart, hide, restore, and export logic
  tests/            # Node built-in test runner, no dependencies
tools/              # Dev utilities (see below)
  mockups/          # Static HTML design explorations
docs/               # Architecture and self-hosting guides
```

### Frontend tests

The frontend is buildless, but the adapter that maps the neutral `/api/v1` feed into FullCalendar's event shape (`js/fullcalendar-adapter.js`) has unit tests on Node's built-in runner — no build step, no `npm install`:

```bash
node --test frontend/tests/*.test.js
```

They lock the price/time string formatting and the `extendedProps` key set that `modal.js`/`filters.js`/`favorites.js` read by name. Backend tests live under `backend/` — see `backend/README.md`.

### Developer tools

Testing scripts are in `tools/` for debugging scrapers and development. Fair warning — these are primarily tested on my machine and some may be outdated.

| Script | What it does |
|---|---|
| `run_scrape.py` | Calls the scrape API and prints a summary; logs to `tools/scrape_results.log` |
| `diagnose_scrapers.py` | Runs scrapers individually and reports output and errors |
| `inspect_html.py` | Fetches and prints raw HTML from a venue page for scraper debugging |
| `inspect_js_venues.py` | Inspects JS-heavy venue pages to find embedded API or widget patterns |
| `check_venue_urls.py` | Spot-checks that venue event page URLs resolve |
| `check_roots.py` | Verifies scraper URL roots are reachable |
| `wait_for_deploy.py` | Polls `/api/health` until a new Cloud Build deploy goes live |
| `import_submissions.py` | Imports approved event submissions from a Google Sheet into the DB |

---

## Scraper map

Each venue is handled by one scraper type. Last updated 2026-09-21 — may drift as venues are added.

| Scraper | Venues |
|---|---|
| `ticketmaster` | Koka Booth Amphitheatre, Red Hat Amphitheater, DPAC, The Ritz |
| `rhp_events` | Lincoln Theatre, Cat's Cradle, Cat's Cradle Back Room, Local 506 |
| `motorco` | Motorco Music Hall |
| `carolina_theatre` | Carolina Theatre |
| `eventprime` | Kings |
| `tribe_events` | The Cave |
| `venuepilot` | Haw River Ballroom, Rubies on Five Points, Stanczyks, The Pinhook |
| `squarespace` | Neptune's Parlour, Boom Club |
| `mec` | Shadowbox Studio, Slim's |
| `tickpick_organizer` | Chapel of Bones |
| `webflow_cms` | Pour House |
| `instantseats` | Sharp 9 Gallery |
| `duke_bedework` | Duke Arts |
| `instantseats` | Sharp 9 Gallery |

The authoritative source is [`backend/app/seed.py`](backend/app/seed.py) — each venue dict has a `scraper_type` field.

---

## Credentials in request URLs

The Ticketmaster Discovery API authenticates with a query parameter (`?apikey=`) rather than a header, so every request URL the Ticketmaster scraper builds *is* a live credential. Four sinks would otherwise carry it out of the process, and closing one leaves the others open:

| Sink | Closed by |
|---|---|
| `httpx` logs every request at INFO with the full query string | `app.main.configure_logging` pins the `httpx` logger to WARNING |
| `httpx.HTTPStatusError` embeds the URL in `str(e)`, which is logged on a failed scrape | `RedactingFormatter`, installed on every log handler in the process (so it covers exception tracebacks too) |
| An exception escaping a *route* is re-raised by Starlette's `ServerErrorMiddleware` and logged by the ASGI server on its own error logger — which has `propagate=False` and its own handler, so it never passes a root handler | the same sweep: `configure_logging` wraps *every* handler, not root's, via `redaction.redact_handler`, which preserves each handler's existing format instead of replacing it |
| That same string is persisted to `scrape_logs.error_message` and returned in the per-venue result dict from `POST /api/scrape` | `manager.scrape_venue` redacts once, before all three uses |
| `POST /api/scrape`'s catch-all also returns `detail=str(e)`, but on failures *outside* `scrape_venue` — session construction, a scraper import — that never pass through its redaction | `main.trigger_scrape` redacts independently, in its own `except` block |

`backend/app/redaction.py::redact_credentials` is the shared helper. It is a **denylist** of parameter names and therefore never complete — a new scraper authenticating with an unlisted parameter needs an entry there and a case in the parametrized test in `backend/tests/test_redaction.py`, not a nearby entry that happens to look similar. Only the value is removed, so a redacted URL still says which venue was being fetched and with what paging.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy (async), Alembic |
| Database | PostgreSQL (asyncpg driver) |
| Scraping | httpx, BeautifulSoup4, Ticketmaster Discovery API |
| Frontend | Vanilla JS, FullCalendar v6 |
| Scheduling | APScheduler (local) / Google Cloud Scheduler (production) |
| Deployment | Google Cloud Run + Neon PostgreSQL + Cloudflare |

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for how a request reaches the app and how a commit becomes a deploy.

---

## Deployment (WXYC fork)

This fork runs on [Railway](https://railway.app) (project `triangle-shows`, `production` environment) rather than the upstream Cloud Run setup. The service builds from this repo, and pushes to `main` deploy automatically via [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml).

Because Railway's GitHub App is not installed on the fork, the workflow drives the deploy through the Railway CLI (`railway redeploy --from-source`) instead of a native Railway deployment trigger. It requires one repo secret:

- **`RAILWAY_TOKEN`** — a Railway *project token* scoped to the `triangle-shows` production environment (Railway dashboard → project → Settings → Tokens).

To deploy a specific commit out of band, run the workflow manually (`workflow_dispatch`) or `railway redeploy --service triangle-shows --from-source --yes` locally against a linked project.

### Service variables

Beyond the `RAILWAY_TOKEN` GitHub Actions secret above, the Railway **service** itself takes optional variables (set via the Railway dashboard or `railway variables`, not a GitHub secret) for internal error capture — see `backend/README.md`'s "Internal error capture" section and `docs/SELF-HOSTING.md`'s "Operating this" for what each one does:

- **`SENTRY_DSN`** — enables the exception tracker. Unset by default; internal errors still reach the container's stderr log stream without it.
- **`ALERT_WEBHOOK_URL`** — a Slack/Mattermost/Discord incoming-webhook URL for scrape-health digests, not internal errors. Inert until that feature ships (issue #86) — setting it now is harmless but silent.
- **`GIT_COMMIT`** — should be set to `${{RAILWAY_GIT_COMMIT_SHA}}` so `GET /api/v1/health` reports a real build SHA instead of `"unknown"`. This is a **gate, not a given**: Railway's GitHub App is not installed on this fork (the deploy workflow uses the CLI instead, above), so the reference variable may not resolve. Check that it actually resolves on the running service before setting it — if it resolves empty, leave `GIT_COMMIT` unset (today's honest `"unknown"` beats a silently empty version string) and file the build-arg fix instead. *Outcome: not yet checked post-merge.*

### Working on the WXYC fork

The fork works on `main` alone: no `dev` or `prod` branch, and no Cloud Build. Pull requests get upstream's CI (`.github/workflows/ci.yml`); a push to `main` deploys to Railway (above), which applies migrations at startup. Upstream's `release.yml` and Cloud Run config are not used here.

Syncing with upstream is a merge, not a rebase, so upstream's commits stay ancestors of `main` and the next sync only has to resolve what changed since. The first sync (WXYC/triangle-shows#129) set the rules later ones should keep:

- **`/api/v1` is a published contract.** WXYC Backend-Service imports `/api/v1/events?dedup=false&include_removed=true` nightly, keyed on `<venue_slug>:<source_key>`. Upstream's read filters (`duplicate_of_id IS NULL`, `is_live_music`) and query-API gate never apply to it.
- **Nothing hard-deletes a scraped row.** Upstream's reconcile pass (which deleted rows a venue stopped listing) is replaced by the fork's identity match and tombstoning snapshot diff, because Backend-Service mirrors `removed_at`.
- **Upstream migrations are renumbered** after the fork's head (upstream `0003`-`0007` are `0010`-`0014` here), chained linearly with no merge revision.
- **Upstream-only product features come in as code but stay off**: show submissions (`SHOW_SUBMISSIONS_ENABLED`, default false), the admin (no `ADMIN_PASSWORD`), ntfy notifications (no `NTFY_URL`), and the `/api/events` query gate (`PUBLIC_QUERY_API_ENABLED`, default true here).
- **`/api/health` does not return upstream's 503 when scrape data is stale**: it is Railway's deploy healthcheck, and a 503 would block the deploy that fixes the scrape.

---

## Roadmap

Upcoming features and ideas are tracked in [GitHub Issues](https://github.com/triangle-shows/triangle-shows/issues). Some things I'm currently thinking about:

- Adding more venues (the Fruit, others)
- Handling custom/one-off events and form submission
- Performance improvements
- Possibly pulling in events from Instagram accounts like Fuzzy Needle

---

## Help me out!

I'm looking for co-developers — this thing could be really cool and TBH I'm not a professional developer. Reach out to me if you want to help build it!

See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for upstream's branch/PR workflow if you want to contribute upstream. For this fork, see "Working on the WXYC fork" below.

---

## License / contact me

[Functional Source License, Version 1.1, ALv2 Future License](LICENSE) (FSL-1.1-ALv2) — matching upstream, which relicensed from GPL v3 on 2026-08-26. Each version converts to Apache 2.0 two years after its release.

This repository is a fork of [triangle-shows/triangle-shows](https://github.com/triangle-shows/triangle-shows). Commits before this change were published under GNU GPL v3; that grant is unaffected for anyone who received the code under it. Code from this point forward is FSL-1.1-ALv2.

The Functional Source License covers the code. The underlying data that populates the calendar is the property of the original sites.

The formatting, curation, and filtering of the dataset is the property of triangle-shows.
The data is available for your own non-competing use; if you use the underlying data, attribute it to triangle-shows.
You can use words like "powered by triangle-shows" with a link back to the original site or this repo.

Contact me at [@tyfi](https://bsky.app/profile/tyfi.bsky.social) on Bluesky, or you can email [mail@triangle-shows.net](mailto:mail@triangle-shows.net).
