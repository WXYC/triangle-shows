# Running Triangle Shows locally

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- A free [Ticketmaster Developer API key](https://developer.ticketmaster.com/) (required for several venues)

## Setup

```bash
git clone https://github.com/ty-fi/triangle-shows
cd triangle-shows

# Copy the example env file and fill in your Ticketmaster API key
cp backend/.env.example backend/.env
```

Open `backend/.env` and replace `your_key_here` with your Ticketmaster API key. The other defaults work as-is for local development:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/triangle_shows
TICKETMASTER_API_KEY=your_key_here   # <-- fill this in
ENABLE_SCHEDULER=false
APP_ENV=development
LOG_LEVEL=INFO
TELEMETRY_SALT=
SENTRY_DSN=
ALERT_WEBHOOK_URL=
```

`TELEMETRY_SALT` salts the hashed client identifier recorded for each `.ics` feed fetch. Leave it empty and feed telemetry is switched off entirely — nothing is recorded. Set it to any random secret to turn it on; the salt is what keeps a stored hash from being brute-forced back to a source IP, so pick a value you keep private and don't change it unless you want the history to become uncorrelatable.

`SENTRY_DSN` and `ALERT_WEBHOOK_URL` are both empty by default and both opt-in — see "Operating this" below for what each one does and doesn't cover.

## Start the app

```bash
docker-compose up
```

This starts two containers: a PostgreSQL database and the FastAPI backend. On first startup it will:

1. Run database migrations (Alembic)
2. Seed the venue list
3. Kick off an initial scrape in the background (takes a minute or two)

The app is available at **http://localhost:8000**.

## Trigger a manual scrape

```bash
# Scrape all venues
curl -X POST http://localhost:8000/api/scrape

# Scrape a single venue type (useful for debugging)
curl -X POST "http://localhost:8000/api/scrape?scraper_type=rhp_events"
```

Scrape results are logged to the database (`ScrapeLog` table) and printed to the Docker console.

## Operating this

Internal errors — a failed migration, a scraper that raised, an unhandled request exception — are captured with a stack trace instead of silently disappearing. Three tiers, each opt-in above the first:

1. **Always on, no configuration.** Every captured error is logged at `ERROR` with a full stack trace on the container's **stderr** (not stdout — point your log drain there). This works with zero accounts and zero env vars.
2. **Push delivery to a chat webhook.** Set `ALERT_WEBHOOK_URL` to a Slack, Mattermost, or Discord (append `/slack` to a Discord webhook URL) incoming-webhook URL and a plain JSON `{"text": ...}` POST is sent. This channel is for **scrape-health digests only** — a periodic summary of which venues' scrapers look broken — not for internal errors, which always go to stderr (and, if configured, the exception tracker below) regardless of this setting. If nothing ever arrives after setting it, that isn't a wiring bug: this variable stays inert until the scrape-health digest feature ships. Leave it empty and the digest is logged instead of posted.
3. **Exception tracking.** Set `SENTRY_DSN` to enable a Sentry-compatible exception tracker. This is entirely optional and fully removable: deleting `backend/app/sentry_hook.py` and `backend/requirements-optional.txt` from a fork drops the dependency completely — the image still builds, the app still boots, and the test suite still passes (with the tracker-specific tests skipped) — while tiers 1 and 2 above keep working unchanged.

None of this requires any of it. A self-hoster who sets nothing gets tier 1 only, which is enough to see what broke and why.
