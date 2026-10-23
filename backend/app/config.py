"""
Pydantic Settings configuration — loads environment variables for the application.

Role: Imported at startup by main.py and any module that needs runtime config (database URL,
scheduler toggle, API keys). The `settings` singleton is created at import time, so .env
must be present (or env vars set) before any module imports this file.
Requires: .env file (or environment variables) providing DATABASE_URL, TICKETMASTER_API_KEY,
ENABLE_SCHEDULER, RUN_STARTUP_SCRAPE, APP_ENV, LOG_LEVEL, TELEMETRY_SALT, SENTRY_DSN, and
ALERT_WEBHOOK_URL.
"""

# --- Imports ---
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


# --- Settings ---

class Settings(BaseSettings):
    """Application-wide configuration, populated from environment variables or .env."""

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/triangle_shows"
    TICKETMASTER_API_KEY: str = ""
    ENABLE_SCHEDULER: bool = False  # Set to True in production to run scrapes on a cron schedule
    # Run a full scrape in the background at startup; set False in tests / manual-seed
    # contexts. Upstream calls this ENABLE_STARTUP_SCRAPE (its CI sets that name), so
    # both env var names are accepted; the fork's name wins if both are set.
    RUN_STARTUP_SCRAPE: bool = Field(
        default=True,
        validation_alias=AliasChoices("RUN_STARTUP_SCRAPE", "ENABLE_STARTUP_SCRAPE"),
    )
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    # Per-deployment secret salting feed_fetches.client_hash (app.api.feeds.record_feed_fetch).
    # Empty (the default) disables feed telemetry entirely, on every deployment: an
    # unsalted hash of ip|ua is brute-forceable back to a source IP, so no salt means
    # no rows rather than privacy-degraded rows. Set it to any random secret to opt in.
    TELEMETRY_SALT: str = ""
    # Opt-in exception tracker (Sentry). Empty (the default) means the feature does
    # not exist, same policy as TELEMETRY_SALT above: no events, not degraded ones.
    # Read only by app/sentry_hook.py — app.main and app.scheduler never see it.
    SENTRY_DSN: str = ""
    # Opt-in push delivery for the scrape-health digest (issue #86, not this PR).
    # Empty means observability.send_alert() logs instead of posting. A generic
    # {"text": ...} JSON POST — no vendor named — is what Slack and Mattermost
    # accept, and Discord accepts it too when the operator appends "/slack".
    ALERT_WEBHOOK_URL: str = ""

    # --- Public query API (upstream issue #62) ---
    # Whether the deprecated queryable read endpoints — GET /api/events and
    # GET /api/events/{id} — answer for anyone. Upstream ships this closed; the fork ships
    # it open (WXYC/triangle-shows#129: upstream's gate comes in as code but stays off).
    # /api/v1 is never gated by it.
    PUBLIC_QUERY_API_ENABLED: bool = True

    # --- Origin enforcement (see app.tokens) ---
    # The Cloud Run service accepts unauthenticated requests, so its .run.app hostnames
    # reach the app without passing Cloudflare. These settings let the app itself verify
    # who a request came from on the two routes that must not be open there.
    #
    # Each gate is inert until configured, deliberately: local dev and CI have no
    # Cloudflare and no Cloud Scheduler in front of them. app.tokens.log_enforcement_state()
    # logs which gates are live on every boot, so "inert" is never silent.

    # Cloudflare Access gate on /admin/*. Both are required to enforce.
    CF_ACCESS_TEAM_DOMAIN: str = ""  # e.g. "triangleshows.cloudflareaccess.com"
    CF_ACCESS_AUD: str = ""  # the Access application's AUD tag

    # Cloudflare Access gate on /new-shows-form/*. A separate Access application from
    # /admin, with its own policy and so its own AUD tag: people allowed to propose a show
    # are not thereby allowed into the admin, and a token minted for one application does
    # not verify against the other. Shares CF_ACCESS_TEAM_DOMAIN, since both applications
    # live in the same Zero Trust team. Unlike /admin there is no password fallback, so in
    # production the form refuses everyone until this is set (see app.api.submissions).
    CF_ACCESS_SUBMIT_AUD: str = ""
    # WXYC fork: upstream's show-submission form comes in as code but stays off
    # (WXYC/triangle-shows#129). Every /new-shows-form route 404s until this is true.
    SHOW_SUBMISSIONS_ENABLED: bool = False

    # --- Notifications for new show submissions (see app.notify) ---
    # Off until NTFY_URL is set. The URL is the full topic URL, e.g.
    # "https://ntfy.sh/ts-submissions-<random>", and on a public ntfy server the topic
    # name is the only thing stopping anyone else reading or posting to it — so it is a
    # secret, and belongs in Secret Manager with NTFY_TOKEN, which is optional: an access
    # token for a server or reserved topic that requires one.
    NTFY_URL: str = ""
    NTFY_TOKEN: str = ""
    # Where the notification's tap-through points. The request's own host cannot be used:
    # the Cloudflare Worker rewrites it to the .run.app origin.
    PUBLIC_SITE_URL: str = "https://triangle-shows.net"

    # Google OIDC gate on POST /api/scrape. Cloud Scheduler already sends the token.
    SCRAPE_ALLOWED_SERVICE_ACCOUNTS: str = ""  # comma-separated service-account emails
    SCRAPE_OIDC_AUDIENCE: str = ""  # optional; the audience configured on the scheduler job

    # Admin subsite (/admin). Both must be set for the admin to be reachable —
    # if ADMIN_PASSWORD is blank, the admin login is disabled (fails closed).
    ADMIN_PASSWORD: str = ""       # shared password exchanged for a session cookie
    SESSION_SECRET: str = ""       # signs the admin session cookie; MUST be set in
                                   # production so cookies validate across instances
                                   # (a random per-process key is used if left blank)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# --- Singleton ---

# Instantiated once at import time; all modules import this object directly.
settings = Settings()
